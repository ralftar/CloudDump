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
    for target in targets:
        r = runner(target, logfile_path)
        if r != 0:
            rc = max(rc, r)
    return rc
