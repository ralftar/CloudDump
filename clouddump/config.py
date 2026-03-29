"""Configuration loading and validation."""

import json
import os
import shutil
import socket
import sys
import urllib.request
import urllib.error
from urllib.parse import urlparse

from clouddump import cfg, log, validate_backup_path
from clouddump.cron import validate_cron


def _check_connectivity(host, port, timeout=5):
    """Test TCP connectivity. Returns True if reachable."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


VALID_GITHUB_ACCOUNT_TYPES = {"org", "user"}


def _check_github(name, token, account_type="org", timeout=10):
    """Verify a GitHub token and account are accessible.

    *account_type* is ``"org"`` (default) or ``"user"``.
    Returns None on success, or an error message string on failure.
    """
    if account_type == "user":
        url = f"https://api.github.com/users/{urllib.request.quote(name, safe='')}"
    else:
        url = f"https://api.github.com/orgs/{urllib.request.quote(name, safe='')}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "CloudDump",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return None
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return "authentication failed (invalid or expired token)"
        if exc.code == 403:
            return "forbidden (token lacks required scopes — needs repo and read:org)"
        if exc.code == 404:
            return f"account '{name}' not found (or token lacks access)"
        return f"HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return f"cannot reach GitHub API: {exc.reason}"

VALID_SMTP_SECURITY = {"ssl", "starttls", "none"}
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
    for field in ("debug", "smtp_ssl", "email_log_attached"):
        val = config.get(field)
        if val is not None and not isinstance(val, bool):
            log.error("Setting '%s' must be true/false (boolean), got %s.", field, type(val).__name__)
            errors += 1

    smtp_security = config.get("smtp_security")
    if smtp_security is not None and smtp_security not in VALID_SMTP_SECURITY:
        log.error("Invalid smtp_security '%s'. Must be one of: %s.",
                  smtp_security, ", ".join(sorted(VALID_SMTP_SECURITY)))
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


def _check_tcp(host, port, job_id, label):
    """TCP connectivity check with consistent logging."""
    if _check_connectivity(host, port):
        log.info("Connectivity OK: %s %s:%s (job %s).", label, host, port, job_id)
    else:
        log.warning("Cannot reach %s %s:%s for job %s.", label, host, port, job_id)


def verify_connectivity(jobs):
    """Run connectivity checks for all jobs (warn only).

    Called after config and job listing have been logged, so the output
    appears in a natural order: config → jobs → verification.
    """
    for job in jobs:
        job_id = cfg(job, "id")
        job_type = cfg(job, "type")
        if not job_id or not job_type:
            continue

        if job_type == "s3bucket":
            for target in cfg(job, "buckets", []):
                endpoint = cfg(target, "endpoint_url")
                if endpoint:
                    # Parse host:port from endpoint URL
                    parsed = urlparse(endpoint)
                    host = parsed.hostname
                    port = parsed.port or (443 if parsed.scheme == "https" else 80)
                    if host:
                        _check_tcp(host, port, job_id, "S3 endpoint")

        if job_type == "azstorage":
            for target in cfg(job, "blobstorages", []):
                source = cfg(target, "source")
                if source:
                    parsed = urlparse(source.split("?")[0])
                    host = parsed.hostname
                    if host:
                        _check_tcp(host, 443, job_id, "Azure Blob")

        if job_type in ("pgsql", "mysql"):
            for target in cfg(job, "servers", []):
                host = cfg(target, "host")
                port = cfg(target, "port", 5432 if job_type == "pgsql" else 3306)
                if host:
                    _check_tcp(host, port, job_id, job_type)

        if job_type == "rsync":
            for target in cfg(job, "targets", []):
                source = cfg(target, "source")
                port = cfg(target, "ssh_port", 22)
                if source and ":" in source:
                    host = source.split(":")[0].split("@")[-1]
                    if host:
                        _check_tcp(host, port, job_id, "SSH")

        if job_type == "github":
            for account in cfg(job, "organizations", []):
                acct_name = cfg(account, "name")
                token = cfg(account, "token")
                acct_type = cfg(account, "account_type", "org")
                if acct_name and token and acct_type in VALID_GITHUB_ACCOUNT_TYPES:
                    gh_err = _check_github(acct_name, token, acct_type)
                    if gh_err:
                        log.warning("GitHub check failed for %s '%s' in job %s: %s",
                                    acct_type, acct_name, job_id, gh_err)
                    else:
                        log.info("GitHub token verified for %s '%s' in job %s.",
                                 acct_type, acct_name, job_id)
