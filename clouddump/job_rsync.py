"""Rsync-over-SSH job runner."""

import os
import re
import shlex
import stat
import subprocess
import tempfile
import time

import clouddump
from clouddump import cfg, log, run_cmd

# user@host:/path — no shell metacharacters anywhere
_SOURCE_RE = re.compile(r"^[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+:/[a-zA-Z0-9_./ -]+$")

# rsync --list-only line: "<perms> <size> YYYY/MM/DD HH:MM:SS <path>"
_LIST_LINE_RE = re.compile(
    r"^(?P<perms>\S+)\s+\S+\s+(?P<date>\d{4}/\d{2}/\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<name>.+)$"
)


def _build_ssh_args(ssh_key, ssh_port):
    """Return the common SSH option list used by rsync.

    rsync hands the ``-e`` value to a shell, so every element here is joined
    into one shell word-list by the callers. ssh_key is operator-supplied and
    unconstrained (unlike ``source``, which _SOURCE_RE pins down), so quote it:
    an unquoted path with a space silently breaks the command, and one with a
    semicolon would inject.
    """
    return [
        "ssh", "-i", shlex.quote(ssh_key), "-p", shlex.quote(ssh_port),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
    ]


def _list_remote_files(host_part, remote_path, ssh_args):
    """List remote regular files via ``rsync --list-only``.

    Uses the rsync protocol itself to enumerate files — no remote shell
    invocation, so it works with restricted accounts (forced commands,
    rrsync, etc.). Returns ``{path: mtime}`` with paths relative to
    *remote_path*, or ``None`` on failure.
    """
    if not remote_path.endswith("/"):
        remote_path += "/"
    ssh_cmd = " ".join(ssh_args)
    cmd = ["rsync", "-rn", "--list-only", "-e", ssh_cmd, f"{host_part}:{remote_path}"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("Remote listing failed (rc %d): %s", proc.returncode, proc.stderr.strip())
        return None

    files = {}
    for line in proc.stdout.splitlines():
        m = _LIST_LINE_RE.match(line)
        if not m or not m.group("perms").startswith("-"):
            continue
        try:
            mtime = time.mktime(time.strptime(
                f"{m.group('date')} {m.group('time')}", "%Y/%m/%d %H:%M:%S"
            ))
        except ValueError:
            continue
        files[m.group("name")] = mtime
    return files


def _find_old_files(host_part, remote_path, min_age_days, ssh_args):
    """List remote files older than *min_age_days*, or ``None`` on failure."""
    files = _list_remote_files(host_part, remote_path, ssh_args)
    if files is None:
        return None
    cutoff = time.time() - (min_age_days * 86400)
    return [name for name, mtime in files.items() if mtime < cutoff]


def _prune_aged_orphans(destination, remote_files, cutoff):
    """Delete local files older than *cutoff* that no longer exist remotely.

    This is ``delete_destination`` for a ``min_age_days`` target: the aged
    part of the destination mirrors the aged part of the source, and
    anything newer than the cutoff is never touched. Directories emptied by
    the pruning are removed as well. Returns the number of files deleted,
    or ``None`` when pruning was refused.
    """
    # An empty listing means the source is gone or unmounted, not that the
    # owner deleted everything. Mirroring that would wipe the backup.
    if not remote_files:
        log.error("Remote listing is empty; refusing to prune %s.", destination)
        return None

    removed = 0
    emptied = set()
    for root, _dirs, names in os.walk(destination):
        for name in names:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, destination).replace(os.sep, "/")
            if rel in remote_files:
                continue
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode) or st.st_mtime >= cutoff:
                continue
            os.remove(path)
            removed += 1
            emptied.add(root)

    # Deepest first, so a parent emptied by its children goes too.
    for d in sorted(emptied, key=len, reverse=True):
        while d != destination and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
    return removed


def _prune(destination, remote_files, cutoff, min_age_days):
    """Run _prune_aged_orphans and turn its result into a job return code."""
    removed = _prune_aged_orphans(destination, remote_files, cutoff)
    if removed is None:
        return 1
    log.info("Pruned %d file(s) older than %d days that no longer exist on remote.",
             removed, min_age_days)
    return 0


def run_rsync_sync(target, logfile_path):
    """Sync a remote directory to a local directory using ``rsync`` over SSH."""
    source = cfg(target, "source")
    destination = cfg(target, "destination")
    ssh_key = cfg(target, "ssh_key")
    ssh_port = str(cfg(target, "ssh_port", "22"))
    delete = cfg(target, "delete_destination", True)
    delete_excluded = cfg(target, "delete_excluded", False)
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
    remote_files = None
    cutoff = None
    if min_age_days:
        host_part, remote_path = source.split(":", 1)
        remote_files = _list_remote_files(host_part, remote_path, ssh_args)
        if remote_files is None:
            return 1
        cutoff = time.time() - (min_age_days * 86400)
        files = [name for name, mtime in remote_files.items() if mtime < cutoff]
        if not files:
            log.info("No files older than %d days found on remote.", min_age_days)
            if delete:
                return _prune(destination, remote_files, cutoff, min_age_days)
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
            # --delete against a --files-from list has no sane meaning here;
            # deletions for min_age_days targets go through _prune instead.
            cmd += ["--files-from", filelist_path]
        elif delete or delete_excluded:
            cmd.append("--delete")
        if delete_excluded:
            # Also purge already-mirrored copies of newly-excluded paths
            # (e.g. regenerable Nextcloud previews) from the destination.
            cmd.append("--delete-excluded")
        for pattern in exclude:
            cmd += ["--exclude", pattern]
        cmd += [source, destination]

        t0 = time.time()
        rc = run_cmd(cmd, logfile_path=logfile_path)
        elapsed = int(time.time() - t0)

        if rc != 0:
            log.error("Rsync failed", extra={"source": source, "elapsed_s": elapsed})
            return rc
        log.info("Rsync completed", extra={"source": source, "elapsed_s": elapsed})
        if filelist_path and delete:
            return _prune(destination, remote_files, cutoff, min_age_days)
        return rc
    finally:
        if filelist_path:
            try:
                os.remove(filelist_path)
            except OSError:
                pass
