"""MySQL dump job runner."""

import os
import shutil
import tempfile
import time
from datetime import datetime, timezone

import clouddump
from clouddump import cfg, log, run_cmd, _safe_remove

# Databases that should never be dumped.
_SYSTEM_DATABASES = {"information_schema", "mysql", "performance_schema", "sys"}


def _list_databases(host, port, user, password):
    """Query the server for a list of databases using mysql -e 'SHOW DATABASES'."""
    env = {**os.environ, "MYSQL_PWD": password}

    fd, tmppath = tempfile.mkstemp(prefix="mysql-list-")
    fd_err, errpath = tempfile.mkstemp(prefix="mysql-err-")
    try:
        with os.fdopen(fd, "w") as tmp, os.fdopen(fd_err, "w") as err:
            rc = run_cmd(
                ["mysql", "-h", host, "-P", str(port), "-u", user,
                 "--batch", "--skip-column-names", "-e", "SHOW DATABASES"],
                env=env, stdout=tmp, stderr=err,
            )
        if rc != 0:
            with open(errpath) as f:
                err_msg = f.read().strip()
            if err_msg:
                log.error("mysql: %s", err_msg)
            return None
        with open(tmppath) as f:
            output = f.read()
    finally:
        _safe_remove(tmppath)
        _safe_remove(errpath)

    return [name.strip() for name in output.splitlines() if name.strip()]


def run_mysql_dump(server, logfile_path):
    """Dump one or more MySQL databases from a server using ``mysqldump``.

    Individual databases are retried on failure (configurable via
    ``db_retries``, default 3).
    """
    host = cfg(server, "host")
    port = str(cfg(server, "port", "3306"))
    user = cfg(server, "user", "root")
    password = cfg(server, "pass")
    backuppath = cfg(server, "backuppath")
    filenamedate = cfg(server, "filenamedate", False)
    compress = cfg(server, "compress", True)
    databases_cfg = cfg(server, "databases", [])
    databases_excluded = cfg(server, "databases_excluded", [])
    max_db_retries = cfg(server, "db_retries", 3)

    if not host or not user or not password or not backuppath:
        log.error("Missing required mysql parameters.")
        return 1

    os.makedirs(backuppath, exist_ok=True)

    log.info("Dumping %s:%s → %s", host, port, backuppath)
    log.debug("Username: %s, filenamedate: %s, compress: %s", user, filenamedate, compress)

    all_dbs = _list_databases(host, port, user, password)
    if all_dbs is None:
        log.error("Failed to query database list from %s.", host)
        return 1

    # Determine which databases to back up
    if databases_cfg:
        log.info("Using explicitly configured databases: %s", " ".join(databases_cfg))
        databases_to_backup = list(databases_cfg)
    else:
        log.debug("Using all databases except excluded and system ones")
        excluded_set = set(databases_excluded) | _SYSTEM_DATABASES
        databases_to_backup = [db for db in all_dbs if db not in excluded_set]

    if not databases_to_backup:
        log.warning("No databases to backup.")
        return 0

    log.info("Databases to backup: %s", " ".join(databases_to_backup))

    env = {**os.environ, "MYSQL_PWD": password}
    overall_result = 0

    for database in databases_to_backup:
        log.debug("Processing database: %s", database)

        cmd = [
            "mysqldump", "-h", host, "-P", port, "-u", user,
            "--single-transaction", "--routines", "--triggers", "--events",
            database,
        ]
        if clouddump.debug:
            cmd.append("--verbose")

        dump_ok = False
        for db_attempt in range(1, max_db_retries + 1):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            temp_file = os.path.join(backuppath, f"{database}-{timestamp}.sql")

            log.debug("Running mysqldump of %s (attempt %d/%d)...", database, db_attempt, max_db_retries)

            with open(temp_file, "w") as dump_out:
                rc = run_cmd(cmd, env=env, stdout=dump_out, logfile_path=logfile_path)

            if rc != 0:
                log.error("mysqldump for %s on %s failed.", database, host)
                _safe_remove(temp_file)
            elif os.path.getsize(temp_file) == 0:
                log.error("Backupfile %s is empty.", temp_file)
                _safe_remove(temp_file)
            else:
                dump_ok = True
                break

            if db_attempt < max_db_retries:
                log.warning("Retrying %s in 30s...", database)
                time.sleep(30)

        if not dump_ok:
            log.error("mysqldump for %s failed after %d attempts.", database, max_db_retries)
            overall_result = 1
            continue

        size = os.path.getsize(temp_file)
        log.info("mysqldump of %s completed. Size: %d bytes.", database, size)

        if filenamedate:
            final_file = temp_file
        else:
            final_file = os.path.join(backuppath, f"{database}.sql")

        if compress:
            log.debug("Compressing backupfile %s...", temp_file)
            rc = run_cmd(["bzip2", "-f", temp_file])
            if rc != 0:
                log.error("Compression of %s failed.", temp_file)
                overall_result = 1
                continue
            temp_file += ".bz2"
            if filenamedate:
                final_file += ".bz2"
            else:
                final_file = os.path.join(backuppath, f"{database}.sql.bz2")
            log.debug("Compression completed. Compressed file: %s", temp_file)

        if temp_file != final_file:
            log.debug("Moving %s to %s...", temp_file, final_file)
            try:
                shutil.move(temp_file, final_file)
            except OSError as exc:
                log.error("Could not move %s to %s: %s", temp_file, final_file, exc)
                overall_result = 1
                continue

        log.debug("Backup completed successfully: %s", final_file)

    if overall_result == 0:
        log.info("All %d database(s) backed up successfully.", len(databases_to_backup))
    else:
        log.warning("Some database backups failed.")
    return overall_result
