"""Configuration loading and validation."""

import json
import os
import shutil
import sys

from clouddump import cfg, log
from clouddump.cron import validate_cron

CONFIG_FILE = "/config/config.json"
VALID_JOB_TYPES = {"s3bucket", "azstorage", "pgsql"}
TOOL_REQUIREMENTS = {
    "s3bucket": ["aws"],
    "azstorage": ["azcopy"],
    "pgsql": ["pg_dump", "psql"],
}


def load_config():
    """Load and parse the JSON configuration file, or exit on failure."""
    if not os.path.isfile(CONFIG_FILE):
        log.error("Missing configuration file %s.", CONFIG_FILE)
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)


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

        crontab = cfg(job, "crontab")
        if not crontab:
            log.error("Missing crontab for job ID %s.", job_id)
            errors += 1
            continue
        cron_error = validate_cron(crontab)
        if cron_error:
            log.error("Invalid crontab '%s' for job ID %s: %s.", crontab, job_id, cron_error)
            errors += 1

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

        summaries.append(f"ID: {job_id}\nType: {job_type}\nSchedule: {crontab}")

    return errors, "\n\n".join(summaries)
