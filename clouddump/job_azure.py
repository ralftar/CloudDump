"""Azure Blob Storage sync job runner."""

import os
import time

from clouddump import cfg, log, run_cmd


def run_az_sync(blobstorage, logfile_path):
    """Sync an Azure Blob Storage container to a local directory using ``azcopy sync``."""
    source = cfg(blobstorage, "source")
    destination = cfg(blobstorage, "destination")
    delete = str(cfg(blobstorage, "delete_destination", "true")).lower() == "true"

    if not source or not destination:
        log.error("Missing source or destination for Azure blobstorage.")
        return 1

    if not source.startswith("https://"):
        log.error("Invalid source. Must start with https://")
        return 1

    source_stripped = source.split("?")[0]
    os.makedirs(destination, exist_ok=True)

    log.debug("Source: %s", source_stripped)
    log.debug("Destination: %s", destination)
    log.debug("Mirror (delete): %s", "true" if delete else "false")
    log.debug("Syncing source %s to destination %s...", source_stripped, destination)

    cmd = ["azcopy", "sync", "--recursive", f"--delete-destination={'true' if delete else 'false'}", source, destination]

    t0 = time.time()
    with open(logfile_path, "a") as logf:
        rc = run_cmd(cmd, stdout=logf, stderr=logf)
    elapsed = int(time.time() - t0)

    if rc != 0:
        log.error("Sync failed after %d seconds.", elapsed)
    else:
        log.debug("Sync completed successfully in %d seconds.", elapsed)
    return rc
