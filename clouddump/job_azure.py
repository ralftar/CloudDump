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

    log.info("Syncing Azure Blob Storage", extra={"source": source_stripped, "destination": destination})

    cmd = ["azcopy", "sync", f"--delete-destination={'true' if delete else 'false'}", source, destination]

    t0 = time.time()
    rc = run_cmd(cmd, logfile_path=logfile_path)
    elapsed = int(time.time() - t0)

    if rc != 0:
        log.error("Azure sync failed", extra={"source": source_stripped, "elapsed_s": elapsed})
    else:
        log.info("Azure sync completed", extra={"source": source_stripped, "elapsed_s": elapsed})
    return rc
