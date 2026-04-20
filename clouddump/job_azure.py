"""Azure Blob Storage sync job runner."""

import os
import time

import clouddump
from clouddump import cfg, log, redact, run_cmd

_AZCOPY_JOB_LOG_DIR = os.path.expanduser("~/.azcopy")


def _append_azcopy_job_log(logfile_path):
    """Append the most recent azcopy per-job log to *logfile_path*.

    Azcopy writes a separate `<uuid>.log` in ``~/.azcopy/`` per invocation
    with the HTTP-level detail requested by ``--log-level=DEBUG``. Its
    stdout only shows the progress summary. When debug is on we surface
    that detail in our own logfile so it reaches the job-report email.
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
             open(logfile_path, "a", encoding="utf-8") as dst:
            dst.write(f"\n--- azcopy job log: {os.path.basename(newest)} ---\n")
            for line in src:
                dst.write(redact(line))
            dst.write("--- end azcopy job log ---\n")
    except OSError as exc:
        log.warning("Could not append azcopy job log %s: %s", newest, exc)


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

    cmd = ["azcopy", "sync", f"--delete-destination={'true' if delete else 'false'}"]
    if clouddump.debug:
        # azcopy's --output-level only supports essential/quiet/default — there
        # is no verbose stdout mode. HTTP-level detail goes to the per-job
        # log file under ~/.azcopy/<uuid>.log when --log-level=DEBUG is set.
        cmd += ["--log-level=DEBUG"]
    cmd += [source, destination]

    t0 = time.time()
    rc = run_cmd(cmd, logfile_path=logfile_path)
    elapsed = int(time.time() - t0)

    if clouddump.debug:
        _append_azcopy_job_log(logfile_path)

    if rc != 0:
        log.error("Azure sync failed", extra={"source": source_stripped, "elapsed_s": elapsed})
    else:
        log.info("Azure sync completed", extra={"source": source_stripped, "elapsed_s": elapsed})
    return rc
