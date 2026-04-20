"""Configuration loading and validation."""

import json
import os
import shutil
import sys
import urllib.parse

from clouddump import cfg, log, validate_backup_path
from clouddump.cron import validate_cron


VALID_GITHUB_ACCOUNT_TYPES = {"org", "user"}


VALID_SMTP_SECURITY = {"ssl", "starttls", "none"}
VALID_LOG_FORMATS = {"text", "json"}
CONFIG_FILE = "/config/config.json"
VALID_JOB_TYPES = {"s3bucket", "azstorage", "pgsql", "mysql", "github", "rsync"}
_VALID_TABLE_FILTER_KEYS = {"tables_included", "tables_excluded"}
TOOL_REQUIREMENTS = {
    "s3bucket": ["aws"],
    "azstorage": ["azcopy"],
    "pgsql": ["pg_dump", "psql"],
    "mysql": ["mysqldump", "mysql"],
    "github": ["github-backup", "git", "curl"],
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

        disabled_tag = "" if cfg(job, "enabled", True) else " (DISABLED)"
        summaries.append(f"ID: {job_id}{disabled_tag}\nType: {job_type}")

    return errors, "\n\n".join(summaries)


def _run_verify(cmd, label, job_id, results, env=None, timeout=15):
    """Run a command and log OK/WARN. Returns the CompletedProcess or None."""
    import subprocess
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        results.append(f"WARN: {label} timed out (job {job_id})")
        log.warning("%s timed out (job %s).", label, job_id)
        return None

    # Separate benign SSH host-key acceptance notices from real errors.
    stderr_lines, host_key_notices = [], []
    for line in proc.stderr.strip().splitlines():
        (host_key_notices if "Permanently added" in line else stderr_lines).append(line)
    for notice in host_key_notices:
        log.info("%s (job %s): %s", label, job_id, notice)

    if proc.returncode == 0:
        results.append(f"OK: {label} (job {job_id})")
        log.info("%s verified (job %s).", label, job_id)
        return proc

    err = stderr_lines[-1] if stderr_lines else "failed"
    results.append(f"WARN: {label} — {err} (job {job_id})")
    log.warning("%s failed (job %s): %s", label, job_id, err)
    return None


def _verify_s3_bucket(job, job_id, results):
    """Verify S3 bucket accessibility and credentials (warn only)."""
    for bucket in cfg(job, "buckets", []):
        source = cfg(bucket, "source")
        if not source:
            continue
        env = {**os.environ}
        for key, envvar in [("aws_access_key_id", "AWS_ACCESS_KEY_ID"),
                            ("aws_secret_access_key", "AWS_SECRET_ACCESS_KEY"),
                            ("aws_region", "AWS_DEFAULT_REGION")]:
            val = cfg(bucket, key)
            if val:
                env[envvar] = val
        # Extract bucket name from s3://bucket/prefix
        bucket_name = source.replace("s3://", "").split("/")[0]
        cmd = ["aws", "s3api", "head-bucket", "--bucket", bucket_name]
        endpoint = cfg(bucket, "endpoint_url")
        if endpoint:
            cmd += ["--endpoint-url", endpoint]
        _run_verify(cmd, f"S3 '{source}'", job_id, results, env=env)


def _verify_az_container(job, job_id, results):
    """Verify Azure Blob Storage container accessibility (warn only).

    Runs ``azcopy list`` so the probe uses the same auth/URL-parsing
    code path as the sync job. A urllib probe can accept a SAS URL
    that ``azcopy`` later rejects (e.g. when the URL's query delimiter
    is mangled), hiding failures until the scheduled job runs.
    """
    for blob in cfg(job, "blobstorages", []):
        source = cfg(blob, "source")
        if not source:
            continue
        label = f"Azure '{source.split('?')[0]}'"
        # azcopy list walks the full blob inventory; containers with ~100k+
        # blobs routinely need well over the 15s default. 150s is a practical
        # ceiling that still bounds startup.
        _run_verify(
            ["azcopy", "list", source, "--output-level=quiet"],
            label, job_id, results, timeout=150,
        )


def _verify_rsync_ssh(job, job_id, results):
    """Verify rsync-over-SSH connectivity (warn only).

    Uses ``rsync --list-only`` so the probe traverses the same rsync
    protocol + SSH transport the sync job uses. This also works with
    restricted remote accounts (forced commands, ``rrsync``) where a
    plain ``ssh test -d`` would fail.
    """
    for target in cfg(job, "targets", []):
        source = cfg(target, "source")
        ssh_key = cfg(target, "ssh_key")
        ssh_port = str(cfg(target, "ssh_port", "22"))
        if not source or not ssh_key or ":" not in source:
            continue
        ssh_cmd = (
            f"ssh -i {ssh_key} -p {ssh_port} "
            "-o StrictHostKeyChecking=accept-new "
            "-o BatchMode=yes -o ConnectTimeout=5"
        )
        _run_verify(
            ["rsync", "-n", "--list-only", "-e", ssh_cmd, f"{source}/"],
            f"rsync '{source}'", job_id, results, timeout=10,
        )


def _verify_db_connection(job, job_id, job_type, results):
    """Verify database credentials with SELECT 1 (warn only)."""
    for server in cfg(job, "servers", []):
        host = cfg(server, "host")
        password = cfg(server, "pass")
        if not host or not password:
            continue
        if job_type == "pgsql":
            port, user = str(cfg(server, "port", "5432")), cfg(server, "user", "postgres")
            env = {**os.environ, "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5"}
            cmd = ["psql", "-h", host, "-p", port, "-U", user,
                   "-d", "postgres", "-t", "-A", "-c", "SELECT 1"]
        else:
            port, user = str(cfg(server, "port", "3306")), cfg(server, "user")
            if not user:
                continue
            env = {**os.environ, "MYSQL_PWD": password}
            cmd = ["mysql", "-h", host, "-P", port, "-u", user,
                   "--batch", "--skip-column-names", "-e", "SELECT 1"]
        _run_verify(cmd, f"{job_type} {user}@{host}:{port}", job_id, results, env=env, timeout=10)


def _verify_github_token(job, job_id, results):
    """Verify GitHub tokens and accounts are accessible (warn only).

    Uses ``curl`` for parity with the other subprocess-based probes and
    to keep all verify paths out of the Python HTTP stack.
    """
    for account in cfg(job, "organizations", []):
        acct_name = cfg(account, "name")
        token = cfg(account, "token")
        acct_type = cfg(account, "account_type", "org")
        if not acct_name or not token or acct_type not in VALID_GITHUB_ACCOUNT_TYPES:
            continue
        endpoint = "users" if acct_type == "user" else "orgs"
        url = f"https://api.github.com/{endpoint}/{urllib.parse.quote(acct_name, safe='')}"
        _run_verify(
            [
                "curl", "-sSf", "--max-time", "10",
                "-H", f"Authorization: Bearer {token}",
                "-H", "Accept: application/vnd.github+json",
                "-H", "User-Agent: CloudDump",
                "-o", os.devnull, url,
            ],
            f"GitHub {acct_type} '{acct_name}'", job_id, results, timeout=15,
        )


def _verify_pgsql(job, job_id, results):
    """Verify configured PostgreSQL databases and table filters (warn only).

    One connection to list databases, then one per database that has table
    filters. Skips table checks if the server is unreachable.
    """
    for server in cfg(job, "servers", []):
        host = cfg(server, "host")
        port = str(cfg(server, "port", "5432"))
        user = cfg(server, "user", "postgres")
        password = cfg(server, "pass")
        if not host or not password:
            continue
        env = {**os.environ, "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5"}

        # Collect configured database names and their table filters
        db_configs = {}  # {dbname: tbl_cfg or {}}
        for entry in cfg(server, "databases", []):
            if isinstance(entry, dict):
                for dbname, tbl_cfg in entry.items():
                    db_configs[dbname] = tbl_cfg if isinstance(tbl_cfg, dict) else {}
        if not db_configs:
            continue

        # Verify databases exist
        proc = _run_verify(
            ["psql", "-h", host, "-p", port, "-U", user, "-d", "postgres", "-t", "-A",
             "-c", "SELECT datname FROM pg_database WHERE datistemplate = false"],
            f"pgsql {user}@{host}:{port}", job_id, results, env=env, timeout=10)
        if not proc:
            continue
        actual_dbs = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
        for db in db_configs:
            if db not in actual_dbs:
                results.append(f"WARN: Database '{db}' not found on {host} (job {job_id})")
                log.warning("Database '%s' not found on %s (job %s).", db, host, job_id)

        # Verify table filters for databases that exist
        for dbname, tbl_cfg in db_configs.items():
            if dbname not in actual_dbs or not tbl_cfg:
                continue
            filters = tbl_cfg.get("tables_included", []) + tbl_cfg.get("tables_excluded", [])
            if not any(t.strip() for t in filters):
                continue
            proc = _run_verify(
                ["psql", "-h", host, "-p", port, "-U", user, "-d", dbname, "-t", "-A",
                 "-c", "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"],
                f"pgsql tables in '{dbname}'@{host}", job_id, results, env=env, timeout=10)
            if not proc:
                continue
            actual_tables = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
            for key in ("tables_included", "tables_excluded"):
                for t in tbl_cfg.get(key, []):
                    t = t.strip()
                    if t and t not in actual_tables:
                        results.append(f"WARN: {key} '{t}' not found in {dbname}@{host} (job {job_id})")
                        log.warning("%s '%s' not found in %s@%s (job %s).", key, t, dbname, host, job_id)


def _verify_mysql(job, job_id, results):
    """Verify configured MySQL databases exist on the server (warn only)."""
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
        proc = _run_verify(
            ["mysql", "-h", host, "-P", port, "-u", user,
             "--batch", "--skip-column-names", "-e", "SHOW DATABASES"],
            f"mysql {user}@{host}:{port}", job_id, results, env=env, timeout=10)
        if not proc:
            continue
        actual = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
        for db in configured_dbs:
            if db not in actual:
                results.append(f"WARN: Database '{db}' not found on {host} (job {job_id})")
                log.warning("Database '%s' not found on %s (job %s).", db, host, job_id)


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
        if not cfg(job, "enabled", True):
            continue

        if job_type == "s3bucket":
            _verify_s3_bucket(job, job_id, results)

        if job_type == "azstorage":
            _verify_az_container(job, job_id, results)

        if job_type == "pgsql":
            if any(cfg(s, "databases", []) for s in cfg(job, "servers", [])):
                _verify_pgsql(job, job_id, results)
            else:
                _verify_db_connection(job, job_id, "pgsql", results)

        if job_type == "mysql":
            if any(cfg(s, "databases", []) for s in cfg(job, "servers", [])):
                _verify_mysql(job, job_id, results)
            else:
                _verify_db_connection(job, job_id, "mysql", results)

        if job_type == "rsync":
            _verify_rsync_ssh(job, job_id, results)

        if job_type == "github":
            _verify_github_token(job, job_id, results)

    return results
