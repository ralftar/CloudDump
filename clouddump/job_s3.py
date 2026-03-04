"""S3 bucket sync job runner."""

import os
import time

from clouddump import cfg, log, run_cmd


def run_s3_sync(bucket, logfile_path):
    """Sync a single S3 bucket to a local directory using ``aws s3 sync``."""
    source = cfg(bucket, "source")
    destination = cfg(bucket, "destination")
    delete = cfg(bucket, "delete_destination", "true")
    key_id = cfg(bucket, "aws_access_key_id")
    secret = cfg(bucket, "aws_secret_access_key")
    region = cfg(bucket, "aws_region", "us-east-1")
    endpoint = cfg(bucket, "endpoint_url")

    if not source or not destination:
        log.error("Missing source or destination for S3 bucket.")
        return 1

    if not source.startswith("s3://"):
        log.error("Invalid source %s. Must start with s3://", source)
        return 1

    if str(delete) not in ("true", "false"):
        delete = "true"

    os.makedirs(destination, exist_ok=True)

    log.debug("Source: %s", source)
    log.debug("Destination: %s", destination)
    log.debug("Mirror (delete): %s", delete)
    log.debug("AWS Region: %s", region)
    if endpoint:
        log.debug("Endpoint URL: %s", endpoint)
    log.debug("Syncing source %s to destination %s...", source, destination)

    env = {**os.environ}
    if key_id:
        env["AWS_ACCESS_KEY_ID"] = key_id
    if secret:
        env["AWS_SECRET_ACCESS_KEY"] = secret
    if region:
        env["AWS_DEFAULT_REGION"] = region

    cmd = ["aws", "s3", "sync"]
    if endpoint:
        cmd += ["--endpoint-url", endpoint]
    if str(delete) == "true":
        cmd.append("--delete")
    cmd += [source, destination]

    t0 = time.time()
    with open(logfile_path, "a") as logf:
        rc = run_cmd(cmd, env=env, stdout=logf, stderr=logf)
    elapsed = int(time.time() - t0)

    if rc != 0:
        log.error("Sync failed after %d seconds.", elapsed)
    else:
        log.debug("Sync completed successfully in %d seconds.", elapsed)
    return rc
