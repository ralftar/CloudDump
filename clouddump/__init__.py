"""Vendanor CloudDump - Backup orchestrator for S3, Azure Storage, and PostgreSQL."""

__version__ = "0.0.0"  # patched by CI/release pipeline

import logging
import os
import re
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Process state (shared across modules)
# ---------------------------------------------------------------------------

child_proc = None          # Currently running subprocess, for signal forwarding
shutdown_requested = False  # Set by signal handler to break the main loop
job_deadline = None        # Unix timestamp; set by main loop before execute_job()
debug = False              # Set by main() from config; enables verbose console output


class JobTimeout(Exception):
    """Raised when a job exceeds its configured timeout."""
    pass

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("clouddump")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def cfg(d, key, default=""):
    """Get value from dict, treating None as *default*."""
    v = d.get(key)
    return default if v is None else v


def redact(text):
    """Remove sensitive values from text for safe logging.

    Covers: password/key/token/secret/credential field patterns,
    AWS access key IDs (AKIA...), Azure connection string secrets
    (AccountKey, SharedAccessKey), and Azure SAS token query parameters.
    """
    text = re.sub(
        r"(password|pass|passwd|pwd|key|token|secret|credential|cred)\s*[:=]\s*\S+",
        r"\1: [REDACTED]", text, flags=re.IGNORECASE,
    )
    text = re.sub(r"AKIA[A-Z0-9]{16}", "[REDACTED_AWS_KEY]", text)
    text = re.sub(
        r"(AccountKey|SharedAccessKey)=[^;]*",
        r"\1=[REDACTED]", text, flags=re.IGNORECASE,
    )
    text = re.sub(r"\?[^?]*(sig|se|st|sp|sr|sv)=[^&?]*", "?[REDACTED]", text)
    return text


def run_cmd(cmd, env=None, stdout=None, stderr=None):
    """Run a command synchronously, tracking it in child_proc for signal forwarding.

    While the process is running, child_proc is set so that _signal_handler
    can terminate it on SIGTERM/SIGINT. Respects job_deadline for timeout.
    Returns the process exit code.
    """
    global child_proc

    proc = subprocess.Popen(cmd, env=env, stdout=stdout, stderr=stderr)
    child_proc = proc

    try:
        remaining = None
        if job_deadline is not None:
            remaining = max(1, job_deadline - time.time())
        proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        child_proc = None
        raise JobTimeout("Job timed out (deadline exceeded)")

    child_proc = None
    return proc.returncode


def _safe_remove(path):
    """Remove a file, ignoring errors if it doesn't exist."""
    try:
        os.remove(path)
    except OSError:
        pass
