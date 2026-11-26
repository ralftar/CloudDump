"""Kopia job runner: snapshots of a local directory in an S3 Kopia repository."""

import contextlib
import hashlib
import os
import shutil
import tempfile
import time

from clouddump import cfg, log, run_cmd

# Kopia's connection config, cache and logs for each repository live under the
# home directory: the container's /tmp is a small tmpfs, too small for the
# cache. They need not outlive the container — a missing config only means the
# next run connects again.
_STATE_ROOT = os.path.join(os.path.expanduser("~"), ".clouddump-kopia")

# Kopia records each snapshot under user@host, and maintenance runs only for
# the owner it names. The container's hostname changes on every start, so
# both are fixed: snapshots stay in one history, and maintenance keeps running.
_IDENTITY = ["--override-username=clouddump", "--override-hostname=clouddump"]

# Snapshot retention, as Kopia's own --keep-* policy flags. Unset keys keep
# Kopia's defaults.
KEEP_KEYS = ("keep_latest", "keep_daily", "keep_weekly", "keep_monthly", "keep_annual")


def repository_label(target):
    """Where the repository is, as endpoint/bucket[/prefix], for reports."""
    parts = [cfg(target, "endpoint") or "?", cfg(target, "bucket") or "?"]
    if cfg(target, "prefix"):
        parts.append(cfg(target, "prefix").strip("/"))
    return "/".join(parts)


def _state_dir(target):
    """One directory per repository, keyed by where the repository is."""
    where = "|".join(cfg(target, k) for k in ("endpoint", "bucket", "prefix"))
    return os.path.join(_STATE_ROOT, hashlib.sha256(where.encode()).hexdigest()[:16])


def _storage_args(target):
    args = ["s3", f"--bucket={cfg(target, 'bucket')}", f"--endpoint={cfg(target, 'endpoint')}"]
    if cfg(target, "prefix"):
        args.append(f"--prefix={cfg(target, 'prefix')}")
    if cfg(target, "region"):
        args.append(f"--region={cfg(target, 'region')}")
    if cfg(target, "disable_tls", False):
        args.append("--disable-tls")
    return args


def _env(target, state):
    # Secrets go to Kopia through its environment, never its arguments, which
    # any process on the host can read.
    return {
        **os.environ,
        "KOPIA_PASSWORD": cfg(target, "password"),
        "AWS_ACCESS_KEY_ID": cfg(target, "access_key"),
        "AWS_SECRET_ACCESS_KEY": cfg(target, "secret_key"),
        "KOPIA_CONFIG_PATH": os.path.join(state, "repository.config"),
        "KOPIA_CACHE_DIRECTORY": os.path.join(state, "cache"),
        "KOPIA_LOG_DIR": os.path.join(state, "logs"),
        "KOPIA_CHECK_FOR_UPDATES": "false",
    }


@contextlib.contextmanager
def readonly_connect(target):
    """Yield ``(cmd, env)`` that connects read-only to the repository.

    In a throwaway state directory, removed afterwards with whatever Kopia
    wrote there, so a probe leaves the job's own connection untouched.
    """
    os.makedirs(_STATE_ROOT, mode=0o700, exist_ok=True)
    state = tempfile.mkdtemp(prefix="probe-", dir=_STATE_ROOT)
    try:
        yield (["kopia", "repository", "connect", *_storage_args(target),
                "--readonly", "--no-persist-credentials"], _env(target, state))
    finally:
        shutil.rmtree(state, ignore_errors=True)


def run_kopia_snapshot(target, logfile_path):
    """Snapshot *source* into the Kopia repository *target* describes.

    Connects to the repository, or creates it when the storage holds none:
    Kopia refuses to create a repository over existing data, so a wrong
    password or key fails rather than starting a second repository beside the
    first. Then sets the retention policy, takes the snapshot and runs full
    maintenance.
    """
    source = cfg(target, "source")
    bucket = cfg(target, "bucket")
    for key in ("source", "endpoint", "bucket", "access_key", "secret_key", "password"):
        if not cfg(target, key):
            log.error("Missing %s for Kopia repository %s.", key, bucket or "?")
            return 1
    if not os.path.isdir(source):
        log.error("Source %s is not a directory.", source)
        return 1

    state = _state_dir(target)
    os.makedirs(state, mode=0o700, exist_ok=True)
    env = _env(target, state)
    storage = _storage_args(target)
    extra = {"source": source, "destination": repository_label(target)}

    def kopia(*args):
        return run_cmd(["kopia", *args], env=env, logfile_path=logfile_path)

    t0 = time.time()
    connected = os.path.isfile(env["KOPIA_CONFIG_PATH"]) and kopia("repository", "status") == 0
    if not connected:
        log.info("Connecting to Kopia repository", extra=extra)
    if not connected and kopia("repository", "connect", *storage, *_IDENTITY) != 0:
        log.info("No repository to connect to; creating one", extra=extra)
        if kopia("repository", "create", *storage, *_IDENTITY) != 0:
            log.error("Could not connect to or create the Kopia repository", extra=extra)
            return 1

    # Maintenance runs only on the client that owns it; this one does.
    steps = [["maintenance", "set", "--owner=me"]]
    keep = [f"--{k.replace('_', '-')}={target[k]}" for k in KEEP_KEYS if k in target]
    if keep:
        steps.append(["policy", "set", "--global", *keep])
    steps.append(["snapshot", "create", source])
    # Full maintenance after every snapshot, rather than when Kopia's 24-hour
    # interval says so: a nightly run lands a little under 24 hours after the
    # last, and would skip it every other night.
    steps.append(["maintenance", "run", "--full"])

    for step in steps:
        rc = kopia(*step)
        if rc != 0:
            log.error("Kopia %s failed", " ".join(step[:2]),
                      extra={**extra, "exit_code": rc, "elapsed_s": int(time.time() - t0)})
            return rc

    log.info("Kopia snapshot completed", extra={**extra, "elapsed_s": int(time.time() - t0)})
    return 0
