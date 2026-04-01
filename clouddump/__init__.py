"""CloudDump - Backup orchestrator for S3, Azure Storage, PostgreSQL, MySQL, and GitHub."""

__version__ = "0.0.0"  # patched by CI/release pipeline

import json as _json
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

# Structured fields that log calls may provide via extra={}
_EXTRA_FIELDS = frozenset({
    "job", "job_type", "attempt", "max_attempts", "elapsed_s",
    "rx_bytes", "tx_bytes", "status", "exit_code",
    "succeeded", "failed", "total",
    "host", "port", "database", "bytes", "database_count",
    "source", "destination",
    "account", "account_type",
})

_LEVEL_NAMES = {"WARNING": "warn", "CRITICAL": "crit"}


_LOG_FORMAT = "[%(asctime)s] level=%(levelname)-7s %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _TextFormatter(logging.Formatter):
    """Human-readable text formatter with short level names and job context."""

    def format(self, record):
        original_level = record.levelname
        original_msg = record.msg
        record.levelname = _LEVEL_NAMES.get(original_level, original_level.lower())
        if current_job:
            record.msg = f"[{current_job}] {record.msg}"
        try:
            result = super().format(record)
            return redact(result)
        finally:
            record.levelname = original_level
            record.msg = original_msg


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON object per line."""

    def format(self, record):
        msg = redact(record.getMessage())

        obj = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": _LEVEL_NAMES.get(record.levelname, record.levelname.lower()),
            "logger": record.name,
            "message": msg,
        }

        # Inject current_job as "job" unless explicitly provided via extra
        if current_job and not hasattr(record, "job"):
            obj["job"] = current_job

        for key in _EXTRA_FIELDS:
            val = getattr(record, key, None)
            if val is not None:
                obj[key] = redact(str(val)) if isinstance(val, str) else val

        if record.exc_info and record.exc_info[0] is not None:
            obj["exception"] = redact(self.formatException(record.exc_info))

        return _json.dumps(obj, default=str)


# Default to text format — switched to json via set_log_format() if configured
_text_fmt = _TextFormatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)

# INFO and below → stdout
_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.setLevel(logging.INFO)
_stdout_handler.addFilter(lambda r: r.levelno < logging.WARNING)
_stdout_handler.setFormatter(_text_fmt)

# WARNING and above → stderr
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
_stderr_handler.setFormatter(_text_fmt)


def set_log_format(fmt):
    """Switch console log format. *fmt* is ``"text"`` or ``"json"``."""
    if fmt == "json":
        formatter = _JsonFormatter()
    else:
        formatter = _text_fmt
    _stdout_handler.setFormatter(formatter)
    _stderr_handler.setFormatter(formatter)


def set_debug(enabled):
    """Enable debug-level output on the console."""
    if enabled:
        logging.getLogger().setLevel(logging.DEBUG)
        _stdout_handler.setLevel(logging.DEBUG)

logging.basicConfig(level=logging.INFO, handlers=[_stdout_handler, _stderr_handler])
log = logging.getLogger("clouddump")
log.setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def cfg(d, key, default=""):
    """Get value from dict, treating None as *default*."""
    v = d.get(key)
    return default if v is None else v


def fmt_bytes(n):
    """Format a byte count as a human-readable string."""
    if n >= 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024 * 1024):.1f} GB"
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / 1024:.1f} KB"


def net_bytes():
    """Read total rx/tx bytes from /proc/net/dev. Returns (rx, tx) or None."""
    try:
        with open("/proc/net/dev") as f:
            rx, tx = 0, 0
            for line in f:
                if ":" not in line or "face" in line:
                    continue
                parts = line.split(":")[1].split()
                rx += int(parts[0])
                tx += int(parts[8])
            return rx, tx
    except (OSError, ValueError, IndexError):
        return None


def redact(text):
    """Remove sensitive values from text for safe logging.

    Covers: unquoted and JSON-quoted field patterns, Authorization headers,
    AWS access key IDs, GitHub tokens (all prefixes), Azure connection
    string secrets, Azure SAS tokens, database connection URLs, and
    PEM-encoded private keys.
    """
    # PEM private keys (must run BEFORE field patterns to avoid partial matches)
    text = re.sub(
        r"-----BEGIN[A-Z \n]*(PRIVATE|ENCRYPTED)[A-Z \n]*KEY-----"
        r"[\s\S]*?"
        r"-----END[A-Z \n]*KEY-----",
        "REDACTED_PRIVATE_KEY", text,
    )
    # JSON-quoted fields: "pass": "secret", "aws_secret_access_key": "value", etc.
    text = re.sub(
        r'("[^"]*(?:password|pass|passwd|pwd|key|token|secret|credential|cred)[^"]*"'
        r'\s*:\s*)"[^"]*"',
        r'\1"REDACTED"', text, flags=re.IGNORECASE,
    )
    # Unquoted field patterns: password=secret, token: value
    text = re.sub(
        r"\b(password|pass|passwd|pwd|token|secret|credential|cred)\s*[:=]\s*\S+",
        r"\1: REDACTED", text, flags=re.IGNORECASE,
    )
    # Authorization headers
    text = re.sub(
        r"(Authorization)\s*:\s*\S+(\s+\S+)?",
        r"\1: REDACTED", text, flags=re.IGNORECASE,
    )
    # AWS access key IDs
    text = re.sub(r"AKIA[A-Z0-9]{16}", "REDACTED_AWS_KEY", text)
    # GitHub tokens (all prefixes: ghp_, gho_, ghu_, ghs_, ghr_, github_pat_)
    text = re.sub(r"gh[pousr]_[A-Za-z0-9_]{36,255}", "REDACTED_GH_TOKEN", text)
    text = re.sub(r"github_pat_[A-Za-z0-9_]{22,255}", "REDACTED_GH_TOKEN", text)
    # Azure connection string secrets
    text = re.sub(
        r"(AccountKey|SharedAccessKey)=[^;]*",
        r"\1=REDACTED", text, flags=re.IGNORECASE,
    )
    # Azure SAS query parameters
    text = re.sub(r"\?[^?]*(sig|se|st|sp|sr|sv)=[^&?]*", "?REDACTED", text)
    # Database connection URLs — match password between first : and last @ before host
    text = re.sub(
        r"(postgres|postgresql|mysql|mongodb|redis|amqp)://([^:]+):(.+)@([^@]+)$",
        r"\1://\2:REDACTED@\4", text, flags=re.IGNORECASE | re.MULTILINE,
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
            for raw_line in pipe:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                log.debug("  %s", redact(line))

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
