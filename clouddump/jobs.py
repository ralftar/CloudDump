"""Job dispatch — routes each job to its type-specific runner."""

from clouddump import cfg, log
from clouddump.job_s3 import run_s3_sync
from clouddump.job_azure import run_az_sync
from clouddump.job_pgsql import run_pg_dump


def execute_job(job, logfile_path):
    """Dispatch a job to the appropriate runner by type. Returns exit code.

    Each job type may contain multiple targets (buckets, blobstorages, servers).
    All targets are attempted even if earlier ones fail; the worst exit code wins.
    """
    job_type = cfg(job, "type")

    if job_type == "s3bucket":
        buckets = cfg(job, "buckets", [])
        if not buckets:
            log.error("No buckets configured.")
            return 1
        rc = 0
        for bucket in buckets:
            r = run_s3_sync(bucket, logfile_path)
            if r != 0:
                rc = r
        return rc

    elif job_type == "azstorage":
        blobstorages = cfg(job, "blobstorages", [])
        if not blobstorages:
            log.error("No blobstorages configured.")
            return 1
        rc = 0
        for bs in blobstorages:
            r = run_az_sync(bs, logfile_path)
            if r != 0:
                rc = r
        return rc

    elif job_type == "pgsql":
        servers = cfg(job, "servers", [])
        if not servers:
            log.error("No servers configured.")
            return 1
        rc = 0
        for server in servers:
            r = run_pg_dump(server, logfile_path)
            if r != 0:
                rc = r
        return rc

    log.error("Unknown job type %s.", job_type)
    return 1
