"""Azure Blob Storage sync job runner."""

import os
import re
import time

import clouddump
from clouddump import cfg, log, redact, run_cmd

_AZCOPY_JOB_LOG_DIR = os.path.expanduser("~/.azcopy")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _copy_azcopy_job_log(sidecar_path):
    """Copy the most recent azcopy per-job log to *sidecar_path*, redacted.

    Azcopy writes a separate `<uuid>.log` in ``~/.azcopy/`` per invocation
    with the HTTP-level detail requested by ``--log-level=DEBUG``. Its
    stdout only shows the progress summary. When debug is on we copy that
    per-job log to a sidecar file next to the attempt's logfile so
    ``send_job_report`` picks it up as its own email attachment.
    """
    try:
        logs = [
            os.path.join(_AZCOPY_JOB_LOG_DIR, f)
            for f in os.listdir(_AZCOPY_JOB_LOG_DIR)
            if f.endswith(".log") and not f.endswith("-scanning.log")
        ]
    except FileNotFoundError:
        return
    if not logs:
        return
    newest = max(logs, key=os.path.getmtime)
    try:
        with open(newest, encoding="utf-8", errors="replace") as src, \
             open(sidecar_path, "w", encoding="utf-8") as dst:
            for line in src:
                dst.write(redact(line))
    except OSError as exc:
        log.warning("Could not copy azcopy job log %s: %s", newest, exc)


def _container_from_source(source):
    """Extract the container name (last path segment before ?) from a blob URL."""
    path = source.split("?", 1)[0]
    return path.rstrip("/").rsplit("/", 1)[-1] or "unknown"


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
    container = _container_from_source(source)
    os.makedirs(destination, exist_ok=True)

    log.info("Syncing blobstorage '%s' → %s", container, destination,
             extra={"container": container, "source": source_stripped, "destination": destination})

    cmd = ["azcopy", "sync", f"--delete-destination={'true' if delete else 'false'}"]
    if clouddump.debug:
        # Azcopy log levels: DEBUG (detailed trace, firehose) | INFO (every
        # request/response — a line per blob) | WARNING | ERROR | NONE.
        # INFO keeps per-request visibility for the email sidecar without the
        # full HTTP body trace DEBUG produces.
        cmd += ["--log-level=INFO"]
    cmd += [source, destination]

    t0 = time.time()
    rc = run_cmd(cmd, logfile_path=logfile_path)
    elapsed = int(time.time() - t0)

    if clouddump.debug:
        safe = _SAFE_NAME_RE.sub("_", container)
        sidecar = f"{logfile_path}.{safe}.azcopy.log"
        _copy_azcopy_job_log(sidecar)

    if rc != 0:
        log.error("Blobstorage '%s' failed in %ds", container, elapsed,
                  extra={"container": container, "source": source_stripped, "elapsed_s": elapsed})
    else:
        log.info("Blobstorage '%s' completed in %ds", container, elapsed,
                 extra={"container": container, "source": source_stripped, "elapsed_s": elapsed})
    return rc
