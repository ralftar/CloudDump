"""Rsync-over-SSH job runner."""

import os
import re
import shlex
import subprocess
import tempfile
import time

import clouddump
from clouddump import cfg, log, run_cmd

# user@host:/path — no shell metacharacters anywhere
_SOURCE_RE = re.compile(r"^[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+:/[a-zA-Z0-9_./ -]+$")


def _build_ssh_args(ssh_key, ssh_port):
    """Return the common SSH option list used by both find and rsync."""
    return [
        "ssh", "-i", ssh_key, "-p", ssh_port,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
    ]


def _find_old_files(host_part, remote_path, min_age_days, ssh_args):
    """SSH to remote and find files older than *min_age_days*.

    Returns a list of paths relative to *remote_path*, or ``None`` on failure.
    """
    # Ensure remote_path ends with / so the relative-path stripping works
    if not remote_path.endswith("/"):
        remote_path += "/"
    safe_path = shlex.quote(remote_path)
    # Try GNU find -printf first; fall back to POSIX find + sed for BSD/macOS
    find_expr = (
        f"find {safe_path} -type f -mtime +{min_age_days} -printf '%P\\n'"
        f" 2>/dev/null || find {safe_path} -type f -mtime +{min_age_days}"
        f" | sed 's|^{remote_path}||'"
    )
    cmd = ssh_args + [host_part, find_expr]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("Remote find failed (rc %d): %s", proc.returncode, proc.stderr.strip())
        return None
    lines = proc.stdout.strip().split("\n") if proc.stdout.strip() else []
    return lines


def run_rsync_sync(target, logfile_path):
    """Sync a remote directory to a local directory using ``rsync`` over SSH."""
    source = cfg(target, "source")
    destination = cfg(target, "destination")
    ssh_key = cfg(target, "ssh_key")
    ssh_port = str(cfg(target, "ssh_port", "22"))
    delete = cfg(target, "delete_destination", True)
    exclude = cfg(target, "exclude", [])
    min_age_days = cfg(target, "min_age_days")

    if not source or not destination:
        log.error("Missing source or destination for rsync target.")
        return 1

    if not _SOURCE_RE.match(source):
        log.error("Invalid source %s. Must match user@host:/path (no special characters).", source)
        return 1

    if not ssh_key:
        log.error("Missing ssh_key for rsync target.")
        return 1

    os.makedirs(destination, exist_ok=True)

    log.info("Syncing via rsync", extra={"source": source, "destination": destination})
    log.debug("SSH key: %s, port: %s", ssh_key, ssh_port)
    if min_age_days:
        log.info("Min age filter: %d days", min_age_days)

    ssh_args = _build_ssh_args(ssh_key, ssh_port)
    ssh_cmd = " ".join(ssh_args)

    # Build file list from remote if min_age_days is set
    filelist_path = None
    if min_age_days:
        host_part, remote_path = source.split(":", 1)
        files = _find_old_files(host_part, remote_path, min_age_days, ssh_args)
        if files is None:
            return 1
        if not files:
            log.info("No files older than %d days found on remote.", min_age_days)
            return 0

        log.info("Found %d file(s) older than %d days.", len(files), min_age_days)
        fd, filelist_path = tempfile.mkstemp(suffix=".txt", prefix="clouddump_rsync_", dir=destination)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(files) + "\n")

    try:
        cmd = ["rsync", "-az"]
        if clouddump.debug:
            cmd.append("-v")
        cmd += ["-e", ssh_cmd]
        if filelist_path:
            cmd += ["--files-from", filelist_path]
        if delete:
            cmd.append("--delete")
        for pattern in exclude:
            cmd += ["--exclude", pattern]
        cmd += [source, destination]

        t0 = time.time()
        rc = run_cmd(cmd, logfile_path=logfile_path)
        elapsed = int(time.time() - t0)

        if rc != 0:
            log.error("Rsync failed", extra={"source": source, "elapsed_s": elapsed})
        else:
            log.info("Rsync completed", extra={"source": source, "elapsed_s": elapsed})
        return rc
    finally:
        if filelist_path:
            try:
                os.remove(filelist_path)
            except OSError:
                pass
