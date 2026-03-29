"""CloudDump - Backup orchestrator for S3, Azure Storage, PostgreSQL, MySQL, and GitHub."""

__version__ = "0.0.0"  # patched by CI/release pipeline

import logging
import os
import re
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Process state (shared across modules)
# ---------------------------------------------------------------------------

child_proc = None          # Currently running subprocess, for signal forwarding
shutdown_requested = False  # Set by signal handler to break the main loop
run_now_requested = False   # Set by SIGUSR1 handler to skip cron check
job_deadline = None        # Unix timestamp; set by main loop before execute_job()
debug = False  # Set by main() from config; enables tool output + debug on console
current_job = ""  # Set by main loop; auto-prefixed to log messages


class JobTimeout(Exception):
    """Raised when a job exceeds its configured timeout."""
    pass

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOG_FORMAT = "[%(asctime)s] level=%(levelname)-7s %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _LevelFormatter(logging.Formatter):
    """Formatter that emits short, lowercase level names (info/warn/error)."""

    _NAMES = {"WARNING": "warn", "CRITICAL": "crit"}

    def format(self, record):
        original_level = record.levelname
        original_msg = record.msg
        record.levelname = self._NAMES.get(original_level, original_level.lower())
        if current_job:
            record.msg = f"[{current_job}] {record.msg}"
        try:
            result = super().format(record)
            return redact(result)
        finally:
            record.levelname = original_level
            record.msg = original_msg


# INFO and below → stdout
_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.setLevel(logging.DEBUG)
_stdout_handler.addFilter(lambda r: r.levelno < logging.WARNING)
_stdout_handler.setFormatter(_LevelFormatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))

# WARNING and above → stderr
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
_stderr_handler.setFormatter(_LevelFormatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))

logging.basicConfig(level=logging.INFO, handlers=[_stdout_handler, _stderr_handler])
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
    text = re.sub(
        r"(Authorization)\s*:\s*\S+(\s+\S+)?",
        r"\1: [REDACTED]", text, flags=re.IGNORECASE,
    )
    text = re.sub(r"AKIA[A-Z0-9]{16}", "[REDACTED_AWS_KEY]", text)
    text = re.sub(
        r"(AccountKey|SharedAccessKey)=[^;]*",
        r"\1=[REDACTED]", text, flags=re.IGNORECASE,
    )
    text = re.sub(r"\?[^?]*(sig|se|st|sp|sr|sv)=[^&?]*", "?[REDACTED]", text)
    # Database connection strings: postgres://user:pass@host, mysql://user:pass@host, etc.
    text = re.sub(
        r"(postgres|postgresql|mysql|mongodb|redis|amqp)://([^:]+):([^@]+)@",
        r"\1://\2:[REDACTED]@", text, flags=re.IGNORECASE,
    )
    return text


def run_cmd(cmd, env=None, stdout=None, stderr=None, logfile_path=None):
    """Run a command synchronously, tracking it in child_proc for signal forwarding.

    While the process is running, child_proc is set so that _signal_handler
    can terminate it on SIGTERM/SIGINT. Respects job_deadline for timeout.

    When *logfile_path* is given, output is streamed to both the log file
    and the console in real-time (instead of being buffered until the job
    finishes).  If *stdout* is also provided (e.g. a dump file), only
    stderr is captured and streamed; otherwise both stdout and stderr are
    combined.

    Returns the process exit code.
    """
    global child_proc
    reader = None

    # Setup: if we have a logfile and stderr isn't spoken for, stream
    # tool output to the logfile (and console in debug mode) via a
    # background thread.  Otherwise just run the process directly.
    streaming = logfile_path is not None and stderr is None
    if streaming:
        if stdout is None:
            # Capture both stdout+stderr combined (e.g. sync tools)
            proc = subprocess.Popen(
                cmd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            pipe = proc.stdout
        else:
            # stdout goes to caller's file (e.g. pg_dump dump file),
            # stream only stderr to the log
            proc = subprocess.Popen(
                cmd, env=env,
                stdout=stdout, stderr=subprocess.PIPE,
            )
            pipe = proc.stderr

        def _stream():
            with open(logfile_path, "a") as logf:
                for raw_line in pipe:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                    logf.write(redact(line) + "\n")
                    logf.flush()
                    log.debug("  %s", line)

        reader = threading.Thread(target=_stream, daemon=True)
        reader.start()
    else:
        proc = subprocess.Popen(cmd, env=env, stdout=stdout, stderr=stderr)

    # Wait with timeout enforcement (shared for both modes)
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
        if reader:
            reader.join(timeout=5)
        raise JobTimeout("Job timed out (deadline exceeded)")

    if reader:
        reader.join(timeout=5)
    child_proc = None
    return proc.returncode


_ALLOWED_BACKUP_PREFIXES = ("/backup", "/mnt", "/tmp")


def validate_backup_path(path):
    """Check that a backup destination is under an allowed prefix.

    Returns None if valid, or an error message if the path is unsafe.
    """
    resolved = os.path.realpath(path)
    if any(resolved == p or resolved.startswith(p + "/") for p in _ALLOWED_BACKUP_PREFIXES):
        return None
    return f"path '{path}' (resolved: '{resolved}') is outside allowed prefixes {_ALLOWED_BACKUP_PREFIXES}"



def _safe_remove(path):
    """Remove a file, ignoring errors if it doesn't exist."""
    try:
        os.remove(path)
    except OSError:
        pass
