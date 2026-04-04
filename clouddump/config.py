"""Configuration loading and validation."""

import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

from clouddump import cfg, log, validate_backup_path
from clouddump.cron import validate_cron


VALID_GITHUB_ACCOUNT_TYPES = {"org", "user"}


VALID_SMTP_SECURITY = {"ssl", "starttls", "none"}
VALID_LOG_FORMATS = {"text", "json"}
CONFIG_FILE = "/config/config.json"
VALID_JOB_TYPES = {"s3bucket", "azstorage", "pgsql", "mysql", "github", "rsync"}
TOOL_REQUIREMENTS = {
    "s3bucket": ["aws"],
    "azstorage": ["azcopy"],
    "pgsql": ["pg_dump", "psql"],
    "mysql": ["mysqldump", "mysql"],
    "github": ["github-backup", "git"],
    "rsync": ["rsync", "ssh"],
}


def load_config():
    """Load and parse the JSON configuration file, or exit on failure."""
    if not os.path.isfile(CONFIG_FILE):
        log.error("Missing configuration file %s.", CONFIG_FILE)
        sys.exit(1)
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        log.error("Invalid JSON in %s: %s", CONFIG_FILE, exc)
        sys.exit(1)


def validate_settings(config):
    """Validate top-level config settings. Returns error count."""
    errors = 0
    for field in ("debug", "email_log_attached", "health_log"):
        val = config.get(field)
        if val is not None and not isinstance(val, bool):
            log.error("Setting '%s' must be true/false (boolean), got %s.", field, type(val).__name__)
            errors += 1

    smtp_security = config.get("smtp_security")
    if smtp_security is not None and smtp_security not in VALID_SMTP_SECURITY:
        log.error("Invalid smtp_security '%s'. Must be one of: %s.",
                  smtp_security, ", ".join(sorted(VALID_SMTP_SECURITY)))
        errors += 1

    log_format = config.get("log_format")
    if log_format is not None and log_format not in VALID_LOG_FORMATS:
        log.error("Invalid log_format '%s'. Must be one of: %s.",
                  log_format, ", ".join(sorted(VALID_LOG_FORMATS)))
        errors += 1

    crontab = config.get("crontab")
    if not crontab:
        log.error("Missing required top-level 'crontab'.")
        errors += 1
    else:
        cron_error = validate_cron(crontab)
        if cron_error:
            log.error("Invalid crontab '%s': %s.", crontab, cron_error)
            errors += 1

    health_port = config.get("health_port")
    if health_port is not None:
        try:
            health_port = int(health_port)
            if not 1 <= health_port <= 65535:
                raise ValueError("must be 1-65535")
        except (ValueError, TypeError) as exc:
            log.error("Invalid health_port '%s': %s.", health_port, exc)
            errors += 1

    return errors


def validate_jobs(jobs):
    """Validate all job configs at startup. Returns (error_count, summary_text).

    Checks: required fields (id, type, crontab), duplicate IDs, type against
    VALID_JOB_TYPES allowlist, cron field count, and required external tools.
    All errors are logged; the caller decides whether to abort.
    """
    errors = 0
    seen_ids = {}
    summaries = []

    for i, job in enumerate(jobs):
        job_id = cfg(job, "id")
        if not job_id:
            log.error("Missing job ID for job index %d.", i)
            errors += 1
            continue

        if job_id in seen_ids:
            log.error("Duplicate job ID '%s' at index %d (first at %d).", job_id, i, seen_ids[job_id])
            errors += 1
        seen_ids[job_id] = i

        job_type = cfg(job, "type")
        if not job_type:
            log.error("Missing type for job ID %s.", job_id)
            errors += 1
            continue
        if job_type not in VALID_JOB_TYPES:
            log.error("Invalid job type '%s' for job ID %s. Must be one of: %s.",
                       job_type, job_id, ", ".join(sorted(VALID_JOB_TYPES)))
            errors += 1
            continue

        for tool in TOOL_REQUIREMENTS.get(job_type, []):
            if not shutil.which(tool):
                log.error("Required tool '%s' not found for job ID %s (type: %s).", tool, job_id, job_type)
                errors += 1

        timeout = cfg(job, "timeout", 604800)
        try:
            timeout = int(timeout)
            if timeout <= 0:
                raise ValueError("must be positive")
        except (ValueError, TypeError) as exc:
            log.error("Invalid timeout '%s' for job ID %s: %s.", timeout, job_id, exc)
            errors += 1

        retries = cfg(job, "retries", 3)
        try:
            retries = int(retries)
            if retries < 1:
                raise ValueError("must be at least 1")
        except (ValueError, TypeError) as exc:
            log.error("Invalid retries '%s' for job ID %s: %s.", retries, job_id, exc)
            errors += 1

        # Validate field types in targets
        _TARGET_BOOLS = {
            "s3bucket": ("buckets", ["delete_destination"]),
            "azstorage": ("blobstorages", ["delete_destination"]),
            "pgsql": ("servers", ["filenamedate", "compress"]),
            "mysql": ("servers", ["filenamedate", "compress"]),
            "github": ("organizations", [
                "include_repos", "include_issues", "include_pulls",
                "include_labels", "include_milestones", "include_releases",
                "include_wikis", "include_forks", "include_archived", "include_lfs",
            ]),
            "rsync": ("targets", ["delete_destination"]),
        }
        _TARGET_INTS = {
            "pgsql": ("servers", ["port", "db_retries"]),
            "mysql": ("servers", ["port", "db_retries"]),
            "rsync": ("targets", ["ssh_port", "min_age_days"]),
        }
        if job_type in _TARGET_BOOLS:
            coll, fields = _TARGET_BOOLS[job_type]
            for target in cfg(job, coll, []):
                for field in fields:
                    val = target.get(field)
                    if val is not None and not isinstance(val, bool):
                        log.error("Field '%s' must be true/false (boolean) in job ID %s, got %s.",
                                  field, job_id, type(val).__name__)
                        errors += 1
        if job_type in _TARGET_INTS:
            coll, fields = _TARGET_INTS[job_type]
            for target in cfg(job, coll, []):
                for field in fields:
                    val = target.get(field)
                    if val is not None and not isinstance(val, int):
                        log.error("Field '%s' must be an integer in job ID %s, got %s.",
                                  field, job_id, type(val).__name__)
                        errors += 1

        # Validate backup paths
        path_keys = {
            "s3bucket": ("buckets", "destination"),
            "azstorage": ("blobstorages", "destination"),
            "pgsql": ("servers", "backuppath"),
            "mysql": ("servers", "backuppath"),
            "github": ("organizations", "destination"),
            "rsync": ("targets", "destination"),
        }
        if job_type in path_keys:
            collection_key, field = path_keys[job_type]
            for target in cfg(job, collection_key, []):
                path_val = cfg(target, field)
                if path_val:
                    err = validate_backup_path(path_val)
                    if err:
                        log.error("Unsafe %s for job ID %s: %s", field, job_id, err)
                        errors += 1

        # Validate PostgreSQL table filter syntax
        _VALID_TABLE_FILTER_KEYS = {"tables_included", "tables_excluded"}
        if job_type == "pgsql":
            for server in cfg(job, "servers", []):
                for entry in cfg(server, "databases", []):
                    if not isinstance(entry, dict):
                        log.error("Database entry must be a dict in job ID %s, got %s.",
                                  job_id, type(entry).__name__)
                        errors += 1
                        continue
                    for dbname, tbl_cfg in entry.items():
                        if tbl_cfg is None:
                            continue
                        if not isinstance(tbl_cfg, dict):
                            log.error("Table filter for '%s' must be a dict in job ID %s, got %s.",
                                      dbname, job_id, type(tbl_cfg).__name__)
                            errors += 1
                            continue
                        unknown = set(tbl_cfg.keys()) - _VALID_TABLE_FILTER_KEYS
                        if unknown:
                            log.warning("Unknown table filter key(s) %s for '%s' in job ID %s. "
                                        "Valid keys: %s.",
                                        ", ".join(sorted(unknown)), dbname, job_id,
                                        ", ".join(sorted(_VALID_TABLE_FILTER_KEYS)))
                        for key in _VALID_TABLE_FILTER_KEYS:
                            val = tbl_cfg.get(key)
                            if val is not None and not isinstance(val, list):
                                log.error("'%s' for '%s' must be a list in job ID %s, got %s.",
                                          key, dbname, job_id, type(val).__name__)
                                errors += 1

        # Validate account_type for GitHub jobs (config error, not connectivity)
        if job_type == "github":
            for account in cfg(job, "organizations", []):
                acct_type = cfg(account, "account_type", "org")
                if acct_type not in VALID_GITHUB_ACCOUNT_TYPES:
                    acct_name = cfg(account, "name")
                    log.error("Invalid account_type '%s' for '%s' in job ID %s. Must be one of: %s.",
                              acct_type, acct_name, job_id, ", ".join(sorted(VALID_GITHUB_ACCOUNT_TYPES)))
                    errors += 1

        summaries.append(f"ID: {job_id}\nType: {job_type}")

    return errors, "\n\n".join(summaries)


def _verify_s3_bucket(job, job_id, results):
    """Verify S3 bucket accessibility and credentials (warn only)."""
    import subprocess

    for bucket in cfg(job, "buckets", []):
        source = cfg(bucket, "source")
        if not source:
            continue

        env = {**os.environ}
        key_id = cfg(bucket, "aws_access_key_id")
        secret = cfg(bucket, "aws_secret_access_key")
        region = cfg(bucket, "aws_region", "us-east-1")
        endpoint = cfg(bucket, "endpoint_url")
        if key_id:
            env["AWS_ACCESS_KEY_ID"] = key_id
        if secret:
            env["AWS_SECRET_ACCESS_KEY"] = secret
        if region:
            env["AWS_DEFAULT_REGION"] = region

        cmd = ["aws", "s3", "ls", source, "--page-size", "1"]
        if endpoint:
            cmd += ["--endpoint-url", endpoint]

        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            msg = f"WARN: S3 bucket '{source}' timed out (job {job_id})"
            log.warning("S3 bucket '%s' timed out (job %s).", source, job_id)
            results.append(msg)
            continue

        if proc.returncode == 0:
            msg = f"OK: S3 bucket '{source}' (job {job_id})"
            log.info("S3 bucket verified: %s (job %s).", source, job_id)
            results.append(msg)
        else:
            err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown error"
            msg = f"WARN: S3 bucket '{source}' — {err} (job {job_id})"
            log.warning("S3 bucket check failed for '%s' (job %s): %s", source, job_id, err)
            results.append(msg)


def _verify_az_container(job, job_id, results):
    """Verify Azure Blob Storage container accessibility (warn only)."""
    import subprocess

    for blob in cfg(job, "blobstorages", []):
        source = cfg(blob, "source")
        if not source:
            continue

        source_display = source.split("?")[0]
        cmd = ["azcopy", "list", source, "--running-tally"]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            msg = f"WARN: Azure container '{source_display}' timed out (job {job_id})"
            log.warning("Azure container '%s' timed out (job %s).", source_display, job_id)
            results.append(msg)
            continue

        if proc.returncode == 0:
            msg = f"OK: Azure container '{source_display}' (job {job_id})"
            log.info("Azure container verified: %s (job %s).", source_display, job_id)
            results.append(msg)
        else:
            err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown error"
            msg = f"WARN: Azure container '{source_display}' — {err} (job {job_id})"
            log.warning("Azure container check failed for '%s' (job %s): %s", source_display, job_id, err)
            results.append(msg)


def _verify_rsync_ssh(job, job_id, results):
    """Verify SSH connectivity and remote path exists (warn only)."""
    import subprocess

    for target in cfg(job, "targets", []):
        source = cfg(target, "source")
        ssh_key = cfg(target, "ssh_key")
        ssh_port = str(cfg(target, "ssh_port", "22"))
        if not source or not ssh_key or ":" not in source:
            continue

        host_part, remote_path = source.split(":", 1)
        cmd = [
            "ssh", "-i", ssh_key, "-p", ssh_port,
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            host_part, f"test -d {remote_path}",
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            msg = f"WARN: SSH to '{host_part}' timed out (job {job_id})"
            log.warning("SSH to '%s' timed out (job %s).", host_part, job_id)
            results.append(msg)
            continue

        if proc.returncode == 0:
            msg = f"OK: SSH '{source}' (job {job_id})"
            log.info("SSH path verified: %s (job %s).", source, job_id)
            results.append(msg)
        else:
            err = proc.stderr.strip() or "path not found or SSH failed"
            msg = f"WARN: SSH '{source}' — {err} (job {job_id})"
            log.warning("SSH check failed for '%s' (job %s): %s", source, job_id, err)
            results.append(msg)


def _verify_db_connection(job, job_id, job_type, results):
    """Verify database credentials by listing databases (warn only).

    Used when no explicit database names are configured — the deepest
    check possible without configured references.
    """
    import subprocess

    for server in cfg(job, "servers", []):
        host = cfg(server, "host")
        password = cfg(server, "pass")
        if not host or not password:
            continue

        if job_type == "pgsql":
            port = str(cfg(server, "port", "5432"))
            user = cfg(server, "user", "postgres")
            env = {**os.environ, "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5"}
            cmd = ["psql", "-h", host, "-p", port, "-U", user,
                   "-d", "postgres", "-t", "-A",
                   "-c", "SELECT 1"]
        else:
            port = str(cfg(server, "port", "3306"))
            user = cfg(server, "user")
            if not user:
                continue
            env = {**os.environ, "MYSQL_PWD": password}
            cmd = ["mysql", "-h", host, "-P", port, "-u", user,
                   "--batch", "--skip-column-names", "-e", "SELECT 1"]

        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            msg = f"WARN: {job_type} connection to {host}:{port} timed out (job {job_id})"
            log.warning("%s connection to %s:%s timed out (job %s).", job_type, host, port, job_id)
            results.append(msg)
            continue

        if proc.returncode == 0:
            msg = f"OK: {job_type} {user}@{host}:{port} (job {job_id})"
            log.info("%s connection verified: %s@%s:%s (job %s).", job_type, user, host, port, job_id)
            results.append(msg)
        else:
            err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "connection failed"
            msg = f"WARN: {job_type} {user}@{host}:{port} — {err} (job {job_id})"
            log.warning("%s connection failed for %s@%s:%s (job %s): %s", job_type, user, host, port, job_id, err)
            results.append(msg)


def _verify_github_token(job, job_id, results):
    """Verify GitHub tokens and accounts are accessible (warn only)."""
    for account in cfg(job, "organizations", []):
        acct_name = cfg(account, "name")
        token = cfg(account, "token")
        acct_type = cfg(account, "account_type", "org")
        if not acct_name or not token or acct_type not in VALID_GITHUB_ACCOUNT_TYPES:
            continue

        if acct_type == "user":
            url = f"https://api.github.com/users/{urllib.request.quote(acct_name, safe='')}"
        else:
            url = f"https://api.github.com/orgs/{urllib.request.quote(acct_name, safe='')}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "CloudDump",
        })
        try:
            with urllib.request.urlopen(req, timeout=10):
                msg = f"OK: GitHub {acct_type} '{acct_name}' (job {job_id})"
                log.info("GitHub token verified for %s '%s' (job %s).", acct_type, acct_name, job_id)
                results.append(msg)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            reason = str(getattr(exc, "reason", exc))
            msg = f"WARN: GitHub {acct_type} '{acct_name}' — {reason} (job {job_id})"
            log.warning("GitHub check failed for %s '%s' (job %s): %s", acct_type, acct_name, job_id, reason)
            results.append(msg)


def _verify_pgsql_databases(job, job_id, results):
    """Check that configured database names exist on the server (warn only)."""
    import os
    import subprocess

    for server in cfg(job, "servers", []):
        host = cfg(server, "host")
        port = str(cfg(server, "port", "5432"))
        user = cfg(server, "user", "postgres")
        password = cfg(server, "pass")
        if not host or not password:
            continue

        configured_dbs = []
        for entry in cfg(server, "databases", []):
            if isinstance(entry, dict):
                configured_dbs.extend(entry.keys())
        if not configured_dbs:
            continue

        env = {**os.environ, "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5"}
        proc = subprocess.run(
            ["psql", "-h", host, "-p", port, "-U", user,
             "-d", "postgres", "-t", "-A",
             "-c", "SELECT datname FROM pg_database WHERE datistemplate = false"],
            env=env, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            continue  # TCP check already warned about connectivity

        actual = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
        for db in configured_dbs:
            if db not in actual:
                msg = f"WARN: Database '{db}' not found on {host} (job {job_id})"
                log.warning("Database '%s' not found on %s (job %s).", db, host, job_id)
                results.append(msg)
            else:
                msg = f"OK: Database '{db}' exists on {host} (job {job_id})"
                log.info("Database '%s' exists on %s (job %s).", db, host, job_id)
                results.append(msg)


def _verify_mysql_databases(job, job_id, results):
    """Check that configured database names exist on the server (warn only)."""
    import os
    import subprocess

    for server in cfg(job, "servers", []):
        host = cfg(server, "host")
        port = str(cfg(server, "port", "3306"))
        user = cfg(server, "user")
        password = cfg(server, "pass")
        if not host or not user or not password:
            continue

        configured_dbs = list(cfg(server, "databases", []))
        if not configured_dbs:
            continue

        env = {**os.environ, "MYSQL_PWD": password}
        proc = subprocess.run(
            ["mysql", "-h", host, "-P", port, "-u", user,
             "--batch", "--skip-column-names", "-e", "SHOW DATABASES"],
            env=env, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            continue  # TCP check already warned about connectivity

        actual = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
        for db in configured_dbs:
            if db not in actual:
                msg = f"WARN: Database '{db}' not found on {host} (job {job_id})"
                log.warning("Database '%s' not found on %s (job %s).", db, host, job_id)
                results.append(msg)
            else:
                msg = f"OK: Database '{db}' exists on {host} (job {job_id})"
                log.info("Database '%s' exists on %s (job %s).", db, host, job_id)
                results.append(msg)


def _verify_pgsql_tables(job, job_id, results):
    """Check that configured table filter names exist in the database (warn only)."""
    import os
    import subprocess

    for server in cfg(job, "servers", []):
        host = cfg(server, "host")
        port = str(cfg(server, "port", "5432"))
        user = cfg(server, "user", "postgres")
        password = cfg(server, "pass")
        if not host or not password:
            continue

        for entry in cfg(server, "databases", []):
            if not isinstance(entry, dict):
                continue
            for dbname, tbl_cfg in entry.items():
                if not tbl_cfg or not isinstance(tbl_cfg, dict):
                    continue
                tables_included = tbl_cfg.get("tables_included", [])
                tables_excluded = tbl_cfg.get("tables_excluded", [])
                if not tables_included and not tables_excluded:
                    continue

                env = {**os.environ, "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5"}
                proc = subprocess.run(
                    ["psql", "-h", host, "-p", port, "-U", user,
                     "-d", dbname, "-t", "-A",
                     "-c", "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"],
                    env=env, capture_output=True, text=True, timeout=10,
                )
                if proc.returncode != 0:
                    msg = f"WARN: Cannot query tables in {dbname}@{host} (job {job_id})"
                    log.warning("Cannot query tables in %s@%s for job %s.", dbname, host, job_id)
                    results.append(msg)
                    continue

                actual = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
                for t in tables_included:
                    t = t.strip()
                    if t and t not in actual:
                        msg = f"WARN: tables_included '{t}' not found in {dbname}@{host} (job {job_id})"
                        log.warning("tables_included '%s' not found in %s@%s (job %s).", t, dbname, host, job_id)
                        results.append(msg)
                for t in tables_excluded:
                    t = t.strip()
                    if t and t not in actual:
                        msg = f"WARN: tables_excluded '{t}' not found in {dbname}@{host} (job {job_id})"
                        log.warning("tables_excluded '%s' not found in %s@%s (job %s).", t, dbname, host, job_id)
                        results.append(msg)


def verify_connectivity(jobs):
    """Run connectivity checks for all jobs (warn only).

    Returns a list of result strings for inclusion in the startup email.
    """
    results = []
    for job in jobs:
        job_id = cfg(job, "id")
        job_type = cfg(job, "type")
        if not job_id or not job_type:
            continue

        if job_type == "s3bucket":
            _verify_s3_bucket(job, job_id, results)

        if job_type == "azstorage":
            _verify_az_container(job, job_id, results)

        if job_type == "pgsql":
            has_configured_dbs = any(
                cfg(s, "databases", []) for s in cfg(job, "servers", []))
            if has_configured_dbs:
                _verify_pgsql_databases(job, job_id, results)
                _verify_pgsql_tables(job, job_id, results)
            else:
                _verify_db_connection(job, job_id, "pgsql", results)

        if job_type == "mysql":
            has_configured_dbs = any(
                cfg(s, "databases", []) for s in cfg(job, "servers", []))
            if has_configured_dbs:
                _verify_mysql_databases(job, job_id, results)
            else:
                _verify_db_connection(job, job_id, "mysql", results)

        if job_type == "rsync":
            _verify_rsync_ssh(job, job_id, results)

        if job_type == "github":
            _verify_github_token(job, job_id, results)

    return results
