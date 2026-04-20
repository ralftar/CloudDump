"""Job dispatch — routes each job to its type-specific runner."""

from clouddump import cfg, log
from clouddump.job_s3 import run_s3_sync
from clouddump.job_azure import run_az_sync
from clouddump.job_pgsql import run_pg_dump
from clouddump.job_github import run_github_backup
from clouddump.job_mysql import run_mysql_dump
from clouddump.job_rsync import run_rsync_sync

_RUNNERS = {
    "s3bucket": ("buckets", run_s3_sync),
    "azstorage": ("blobstorages", run_az_sync),
    "pgsql": ("servers", run_pg_dump),
    "mysql": ("servers", run_mysql_dump),
    "github": ("organizations", run_github_backup),
    "rsync": ("targets", run_rsync_sync),
}

# For the per-target summary at the end of each attempt. The field we read is
# typically enough to identify the target ("asset", "db.example.com", etc.).
_TARGET_LABEL_FIELDS = {
    "s3bucket": "source",
    "azstorage": "source",
    "pgsql": "host",
    "mysql": "host",
    "github": "name",
    "rsync": "source",
}


def _target_label(target, job_type):
    field = _TARGET_LABEL_FIELDS.get(job_type)
    if not field:
        return "?"
    val = cfg(target, field) or "?"
    if job_type in ("s3bucket", "azstorage", "rsync"):
        # Strip query strings and protocol noise so the label fits one line.
        val = val.split("?", 1)[0]
    return val


def execute_job(job, logfile_path):
    """Dispatch a job to the appropriate runner by type. Returns exit code.

    Each job type may contain multiple targets (buckets, blobstorages, servers).
    All targets are attempted even if earlier ones fail; the worst exit code wins.
    """
    job_type = cfg(job, "type")

    entry = _RUNNERS.get(job_type)
    if entry is None:
        log.error("Unknown job type %s.", job_type)
        return 1

    key, runner = entry
    targets = cfg(job, key, [])
    if not targets:
        log.error("No %s configured.", key)
        return 1

    rc = 0
    results = []
    for target in targets:
        r = runner(target, logfile_path)
        results.append((_target_label(target, job_type), r))
        if r != 0:
            rc = max(rc, r)

    if len(results) > 1:
        lines = [f"  {'OK  ' if r == 0 else 'FAIL'}  {label}" for label, r in results]
        ok = sum(1 for _, r in results if r == 0)
        log.info("Job summary (%d/%d OK):\n%s", ok, len(results), "\n".join(lines))
    return rc
