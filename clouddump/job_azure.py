"""Azure Blob Storage sync job runner."""

import os
import time

from clouddump import cfg, log, run_cmd


def run_az_sync(blobstorage, logfile_path):
    """Sync an Azure Blob Storage container to a local directory using ``azcopy sync``."""
    source = cfg(blobstorage, "source")
    destination = cfg(blobstorage, "destination")
    delete = cfg(blobstorage, "delete_destination", True)

    if not source or not destination:
        log.error("Missing source or destination for Azure blobstorage.")
        return 1

    if not source.startswith("https://"):
        log.error("Invalid source. Must start with https://")
        return 1

    source_stripped = source.split("?")[0]
    os.makedirs(destination, exist_ok=True)

    log.info("Syncing %s → %s (delete=%s)", source_stripped, destination, delete)

    cmd = ["azcopy", "sync", "--recursive", f"--delete-destination={'true' if delete else 'false'}", source, destination]

    t0 = time.time()
    rc = run_cmd(cmd, logfile_path=logfile_path)
    elapsed = int(time.time() - t0)

    if rc != 0:
        log.error("Sync of %s failed after %ds.", source_stripped, elapsed)
    else:
        log.info("Sync of %s completed in %ds.", source_stripped, elapsed)
    return rc
