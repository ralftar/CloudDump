"""PostgreSQL dump job runner."""

import os
import subprocess
import time
from datetime import datetime, timezone

import clouddump
from clouddump import cfg, fmt_bytes, log, run_cmd, _safe_remove

# In-progress dumps are staged with a `.tmp` suffix; atomic rename is the final
# step. Anything with `.tmp` on disk is by definition a partial dump.
_TMP_SUFFIXES = (".dump.tmp", ".dump.tmp.bz2")

# Databases that should never be dumped.
_SYSTEM_DATABASES = {"template0", "template1", "postgres"}

# Azure PG silently drops idle connections; these detect dead sockets in ~80s
# instead of Linux's 2h tcp_keepalive_time default.
_KEEPALIVE_OPTS = {
    "keepalives": "1",
    "keepalives_idle": "30",
    "keepalives_interval": "10",
    "keepalives_count": "5",
}


def _conninfo(host, port, user, dbname):
    parts = [f"host={host}", f"port={port}", f"user={user}", f"dbname={dbname}"]
    parts.extend(f"{k}={v}" for k, v in _KEEPALIVE_OPTS.items())
    return " ".join(parts)


def _cleanup_tmp_files(backuppath):
    try:
        entries = os.listdir(backuppath)
    except OSError:
        return
    for name in entries:
        if name.endswith(_TMP_SUFFIXES):
            path = os.path.join(backuppath, name)
            log.warning("Removing stale staging file: %s", name)
            _safe_remove(path)


def _list_databases(host, port, user, password):
    """Query the server for a list of databases via a direct SQL query.

    Uses a ``SELECT`` on ``pg_database`` instead of ``psql -l`` so that the
    command works across all PostgreSQL versions (the ``-l`` flag relies on
    internal catalogue columns that were renamed in PostgreSQL 16).
    """
    env = {**os.environ, "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "30"}

    proc = subprocess.run(
        ["psql", "-d", _conninfo(host, str(port), user, "postgres"),
         "-t", "-A",
         "-c", "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname"],
        env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        err_msg = proc.stderr.strip()
        if err_msg:
            log.error("psql: %s", err_msg)
        return None

    databases = []
    for line in proc.stdout.splitlines():
        name = line.strip()
        if name:
            databases.append(name)
    return databases


def run_pg_dump(server, logfile_path):
    """Dump one or more PostgreSQL databases from a server using ``pg_dump``.

    Individual databases are retried on failure (configurable via
    ``db_retries``, default 3).
    """
    host = cfg(server, "host")
    port = str(cfg(server, "port", "5432"))
    user = cfg(server, "user", "postgres")
    password = cfg(server, "pass")
    backuppath = cfg(server, "backuppath")
    filenamedate = cfg(server, "filenamedate", False)
    compress = cfg(server, "compress", True)
    databases_cfg = cfg(server, "databases", [])
    databases_excluded = cfg(server, "databases_excluded", [])
    max_db_retries = cfg(server, "db_retries", 3)

    if not host or not user or not password or not backuppath:
        log.error("Missing required pgsql parameters.")
        return 1

    os.makedirs(backuppath, exist_ok=True)
    _cleanup_tmp_files(backuppath)

    log.info("Dumping PostgreSQL server", extra={"host": host, "port": int(port), "destination": backuppath})
    log.debug("Username: %s, filenamedate: %s, compress: %s", user, filenamedate, compress)

    all_dbs = _list_databases(host, port, user, password)
    if all_dbs is None:
        log.error("Failed to query database list from %s.", host)
        return 1

    # Determine which databases to back up
    configured_dbs = []
    db_table_configs = {}
    for entry in databases_cfg:
        if isinstance(entry, dict):
            for dbname, tbl_cfg in entry.items():
                configured_dbs.append(dbname)
                db_table_configs[dbname] = tbl_cfg or {}

    if configured_dbs:
        log.info("Using explicitly configured databases: %s", " ".join(configured_dbs))
        databases_to_backup = configured_dbs
    else:
        log.debug("Using all databases except excluded and system ones")
        excluded_set = set(databases_excluded) | _SYSTEM_DATABASES
        databases_to_backup = [db for db in all_dbs if db not in excluded_set]

    if not databases_to_backup:
        log.warning("No databases to backup.")
        return 0

    log.info("Databases to backup: %s", " ".join(databases_to_backup),
             extra={"database_count": len(databases_to_backup)})

    env = {**os.environ, "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "30"}
    overall_result = 0
    total_bytes = 0

    for database in databases_to_backup:
        log.debug("Processing database: %s", database)

        tbl_cfg = db_table_configs.get(database, {})
        tables_included = tbl_cfg.get("tables_included", [])
        tables_excluded = tbl_cfg.get("tables_excluded", [])

        cmd = ["pg_dump", "-d", _conninfo(host, port, user, database), "-F", "custom"]
        if clouddump.debug:
            cmd.append("-v")
        for t in tables_included:
            t = t.strip()
            if t:
                cmd += ["--table", t]
        for t in tables_excluded:
            t = t.strip()
            if t:
                cmd += ["--exclude-table", t]

        # Stable staging path — `.tmp` suffix marks in-progress until atomic rename.
        staging = os.path.join(backuppath, f"{database}.dump.tmp")

        dump_ok = False
        for db_attempt in range(1, max_db_retries + 1):
            log.debug("Running pg_dump of %s (attempt %d/%d)...", database, db_attempt, max_db_retries)

            with open(staging, "wb") as dump_out:
                rc = run_cmd(cmd, env=env, stdout=dump_out, logfile_path=logfile_path)

            if rc != 0:
                log.error("pg_dump for %s on %s failed.", database, host)
                _safe_remove(staging)
            elif os.path.getsize(staging) == 0:
                log.error("Backupfile %s is empty.", staging)
                _safe_remove(staging)
            else:
                dump_ok = True
                break

            if db_attempt < max_db_retries:
                log.warning("Retrying %s in 30s...", database)
                time.sleep(30)

        if not dump_ok:
            log.error("pg_dump for %s failed after %d attempts.", database, max_db_retries)
            overall_result = 1
            continue

        size = os.path.getsize(staging)
        total_bytes += size
        log.info("pg_dump completed", extra={"database": database, "bytes": size})

        if compress:
            log.debug("Compressing %s...", staging)
            rc = run_cmd(["bzip2", "-f", staging])
            if rc != 0:
                log.error("Compression of %s failed.", staging)
                _safe_remove(staging)
                _safe_remove(staging + ".bz2")
                overall_result = 1
                continue
            staging += ".bz2"

        # Atomic rename is the final step. Anything that doesn't reach this line
        # leaves a .tmp file that the next cleanup pass will remove.
        if filenamedate:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            basename = f"{database}-{timestamp}.dump"
        else:
            basename = f"{database}.dump"
        if compress:
            basename += ".bz2"
        final_file = os.path.join(backuppath, basename)

        try:
            os.replace(staging, final_file)
        except OSError as exc:
            log.error("Could not rename %s to %s: %s", staging, final_file, exc)
            _safe_remove(staging)
            overall_result = 1
            continue

        log.debug("Backup completed successfully: %s", final_file)

    _cleanup_tmp_files(backuppath)

    if total_bytes > 0:
        log.info("Total dump size: %s", fmt_bytes(total_bytes), extra={"bytes": total_bytes})
    if overall_result == 0:
        log.info("All %d database(s) backed up successfully.", len(databases_to_backup))
    else:
        log.warning("Some database backups failed.")
    return overall_result
