"""Azure Blob Storage sync job runner."""

import os
import re
import time

import clouddump
from clouddump import cfg, log, redact, run_cmd, _safe_remove

_AZCOPY_JOB_LOG_DIR = os.path.expanduser("~/.azcopy")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")
_AZCOPY_STALE_SECONDS = 7 * 24 * 60 * 60


def _list_azcopy_logs():
    try:
        return [
            os.path.join(_AZCOPY_JOB_LOG_DIR, f)
            for f in os.listdir(_AZCOPY_JOB_LOG_DIR)
            if f.endswith(".log")
        ]
    except FileNotFoundError:
        return []


def _copy_azcopy_job_log(sidecar_path):
    """Copy the most recent azcopy per-job log to *sidecar_path*, redacted.

    After copying, remove the azcopy-side log(s) for that invocation so
    ``~/.azcopy/`` doesn't accumulate across runs inside a long-lived
    container. If the copy fails, leave the source in place so the user
    can inspect it directly.
    """
    logs = [p for p in _list_azcopy_logs() if not p.endswith("-scanning.log")]
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
        return

    # Source copied safely — drop the invocation's logs. Each azcopy run
    # writes <uuid>.log plus an optional <uuid>-scanning.log; clean up both.
    _safe_remove(newest)
    scanning = newest[:-len(".log")] + "-scanning.log"
    _safe_remove(scanning)


def prune_stale_azcopy_logs(max_age_seconds=_AZCOPY_STALE_SECONDS):
    """Remove ``~/.azcopy/*.log`` files older than *max_age_seconds*.

    Intended to run at startup to clean up logs left behind by previous
    container incarnations or runs that crashed before the per-invocation
    cleanup in :func:`_copy_azcopy_job_log` could fire.
    """
    now = time.time()
    removed = 0
    for path in _list_azcopy_logs():
        try:
            if now - os.path.getmtime(path) > max_age_seconds:
                _safe_remove(path)
                removed += 1
        except OSError:
            pass
    if removed:
        log.info("Pruned %d stale azcopy log file(s) from %s",
                 removed, _AZCOPY_JOB_LOG_DIR)


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
