"""Rsync-over-SSH job runner."""

import os
import time

import clouddump
from clouddump import cfg, log, run_cmd


def run_rsync_sync(target, logfile_path):
    """Sync a remote directory to a local directory using ``rsync`` over SSH."""
    source = cfg(target, "source")
    destination = cfg(target, "destination")
    ssh_key = cfg(target, "ssh_key")
    ssh_port = str(cfg(target, "ssh_port", "22"))
    delete = cfg(target, "delete_destination", True)
    exclude = cfg(target, "exclude", [])

    if not source or not destination:
        log.error("Missing source or destination for rsync target.")
        return 1

    if ":" not in source:
        log.error("Invalid source %s. Must contain ':' (e.g. user@host:/path).", source)
        return 1

    if not ssh_key:
        log.error("Missing ssh_key for rsync target.")
        return 1

    os.makedirs(destination, exist_ok=True)

    log.info("Source: %s", source)
    log.info("Destination: %s", destination)
    log.info("SSH key: %s", ssh_key)
    log.info("SSH port: %s", ssh_port)
    log.info("Mirror (delete): %s", "true" if delete else "false")
    log.info("Syncing %s to %s...", source, destination)

    ssh_cmd = (
        f"ssh -i {ssh_key} -p {ssh_port}"
        " -o StrictHostKeyChecking=accept-new"
        " -o BatchMode=yes"
    )

    cmd = ["rsync", "-az"]
    if clouddump.debug:
        cmd.append("-v")
    cmd += ["-e", ssh_cmd]
    if delete:
        cmd.append("--delete")
    for pattern in exclude:
        cmd += ["--exclude", pattern]
    cmd += [source, destination]

    t0 = time.time()
    rc = run_cmd(cmd, logfile_path=logfile_path)
    elapsed = int(time.time() - t0)

    if rc != 0:
        log.error("Rsync of %s failed after %ds.", source, elapsed)
    else:
        log.info("Rsync of %s completed in %ds.", source, elapsed)
    return rc
