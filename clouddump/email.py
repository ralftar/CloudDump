"""Email reporting and notification."""

import gzip
import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from clouddump import cfg, fmt_bytes, log, redact


DEFAULT_EMAIL_SIZE_LIMIT_MB = 15


def _resolve_smtp_security(config):
    """Determine SMTP security mode from config.

    Supports ``smtp_security`` field (``"ssl"``, ``"starttls"``,
    ``"none"``).  Default is ``"ssl"``.
    """
    return cfg(config, "smtp_security") or "ssl"


def _build_message(mail_from, recipients, subject, body, parts):
    """Assemble a MIMEMultipart message from prepared attachment parts.

    *parts* is a list of ``(filename, payload_bytes)`` tuples; each is
    attached as a base64-encoded MIMEApplication.
    """
    msg = MIMEMultipart()
    msg["From"] = mail_from
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    for filename, payload in parts:
        part = MIMEApplication(payload, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)
    return msg


def _enforce_size_limit(config, mail_from, recipients, subject, body, raw_parts):
    """Build the message, keeping it under ``email_size_limit_mb``.

    The size is measured on the assembled, base64-encoded message — i.e. what
    the SMTP server actually receives. Staged fallback when it exceeds the
    limit:

    1. gzip each attachment individually;
    2. if still over, drop all attachments and log an error.

    The report body is always sent — only attachments are compressed or
    dropped, so the job is never silently un-reported.
    """
    try:
        limit_mb = int(cfg(config, "email_size_limit_mb", DEFAULT_EMAIL_SIZE_LIMIT_MB))
    except (ValueError, TypeError):
        limit_mb = DEFAULT_EMAIL_SIZE_LIMIT_MB
    limit = limit_mb * 1024 * 1024

    msg = _build_message(mail_from, recipients, subject, body, raw_parts)
    if not raw_parts or len(msg.as_bytes()) <= limit:
        return msg

    # Stage 1: compress each attachment individually.
    gz_parts = [(f"{name}.gz", gzip.compress(payload)) for name, payload in raw_parts]
    msg = _build_message(mail_from, recipients, subject, body, gz_parts)
    if len(msg.as_bytes()) <= limit:
        log.info("Email attachments exceeded %d MB; compressed to fit.", limit_mb)
        return msg

    # Stage 2: still too large — drop attachments, but make the omission
    # explicit in the body so the report never hides that a log is missing.
    log.error(
        "Email attachments exceed the %d MB limit even after compression; "
        "sending report without attachments.", limit_mb,
    )
    notice = (
        f"WARNING: log attachment(s) omitted — exceeded the {limit_mb} MB email "
        f"size limit even after compression. Retrieve the full log from the "
        f"container/host logs.\n\n"
        f"{'-' * 60}\n\n"
    )
    return _build_message(mail_from, recipients, subject, notice + body, [])


def send_email(config, subject, body, attachments=None):
    """Send an email via SMTP with optional file attachments.

    Encryption modes (``smtp_security``):

    - ``"ssl"`` (default) — SMTP_SSL, typically port 465.
    - ``"starttls"`` — STARTTLS upgrade, typically port 587.
    - ``"none"`` — plain SMTP, no encryption (local relays only).

    Silently skips if smtp_server, smtp_port, or mail_to are not configured.
    Logs but does not raise on send failure.
    """
    smtp_server = cfg(config, "smtp_server")
    smtp_port = cfg(config, "smtp_port")
    smtp_user = cfg(config, "smtp_user")
    smtp_pass = cfg(config, "smtp_pass")
    security = _resolve_smtp_security(config)
    mail_from = cfg(config, "mail_from")
    mail_to = cfg(config, "mail_to")

    if not smtp_server or not smtp_port or not mail_to:
        return None  # Not configured

    if not mail_from:
        log.error("smtp_server/mail_to configured but mail_from is missing.")
        return False

    try:
        smtp_port = int(smtp_port)
    except (ValueError, TypeError):
        log.error("Invalid SMTP port '%s', skipping email.", smtp_port)
        return None

    # Support multiple recipients: comma-separated string or list.
    if isinstance(mail_to, list):
        recipients = [r.strip() for r in mail_to if r.strip()]
    else:
        recipients = [r.strip() for r in mail_to.split(",") if r.strip()]

    # Read + redact attachment contents into memory so the final message size
    # can be measured and, if needed, compressed or dropped before sending.
    raw_parts = []  # (filename, payload_bytes)
    for entry in attachments or []:
        if isinstance(entry, tuple):
            path, name = entry
        else:
            path, name = entry, os.path.basename(entry)
        if os.path.isfile(path):
            with open(path, "r") as f:
                content = redact(f.read())
            raw_parts.append((name, content.encode("utf-8")))

    msg = _enforce_size_limit(config, mail_from, recipients, subject, body, raw_parts)

    log.info("Sending email to %s from %s.", ", ".join(recipients), mail_from)
    try:
        if security == "ssl":
            srv = smtplib.SMTP_SSL(smtp_server, smtp_port)
        else:
            srv = smtplib.SMTP(smtp_server, smtp_port)
            if security == "starttls":
                srv.starttls()
        with srv:
            if smtp_user and smtp_pass:
                srv.login(smtp_user, smtp_pass)
            srv.sendmail(mail_from, recipients, msg.as_string())
    except Exception as exc:
        log.error("Failed to send email: %s", exc, exc_info=True)
        return False
    log.debug("Email sent to %s (%s).", ", ".join(recipients), fmt_bytes(len(msg.as_bytes())))
    return True


def format_job_config(job):
    """Return a readable, redacted representation of a job's configuration."""
    return redact(json.dumps(job, indent=2, default=str))


def send_job_report(config, version, host, job, exit_code, t_start, t_end, logfile_paths,
                    status=None, attempts_used=None, max_attempts=None):
    """Send job completion email, optionally with log attachments.

    *logfile_paths* is a list of log files (one per attempt).  All are
    attached when ``email_log_attached`` is true in config.

    *status* is one of ``"Success"``, ``"Warning"``, or ``"Failure"``.
    """
    email_log_attached = cfg(config, "email_log_attached", False)
    job_id = cfg(job, "id")
    job_type = cfg(job, "type")
    if status is None:
        status = "Success" if exit_code == 0 else "Failure"
    elapsed = int(t_end - t_start)
    minutes, seconds = divmod(elapsed, 60)
    start_str = datetime.fromtimestamp(t_start, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    end_str = datetime.fromtimestamp(t_end, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    job_config_text = format_job_config(job)

    attempt_info = ""
    if attempts_used is not None and max_attempts is not None:
        attempt_info = f"Attempts: {attempts_used}/{max_attempts}\n"

    summary = f"{status} | {job_id} ({job_type}) | {minutes}m {seconds}s"
    if attempts_used is not None and max_attempts is not None:
        summary += f" | attempt {attempts_used}/{max_attempts}"
    log.info("Job report: %s", summary)

    body = (
        f"CloudDump job at {host} has completed!\n\n"
        f"JOB REPORT\n\n"
        f"Result: {status}\n"
        f"Job: {job_id} ({job_type})\n"
        f"{attempt_info}"
        f"Started: {start_str}\n"
        f"Completed: {end_str}\n"
        f"Time elapsed: {minutes} minutes {seconds} seconds\n"
        f"\n"
        f"CONFIGURATION\n\n"
        f"{job_config_text}\n\n"
        f"{'See attached log(s) for details.' if email_log_attached else 'Logs available when email_log_attached is set to true.'}\n\n"
        f"----\n"
        f"CloudDump v{version}\n"
        f"https://github.com/ralftar/CloudDump\n"
    )

    attachments = []
    if email_log_attached:
        import glob
        # Support both a single path (string) and a list of paths
        paths = logfile_paths if isinstance(logfile_paths, list) else [logfile_paths]
        timestamp = datetime.fromtimestamp(t_start, tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        for i, path in enumerate(paths, 1):
            if os.path.isfile(path):
                suffix = f"-attempt{i}" if len(paths) > 1 else ""
                name = f"clouddump-{job_id}-{timestamp}{suffix}.log"
                attachments.append((path, name))
            # Sidecars written by runners (e.g. job_azure's azcopy job log).
            for sidecar in sorted(glob.glob(f"{path}.*.log")):
                if os.path.isfile(sidecar):
                    tag = sidecar[len(path) + 1:-len(".log")]
                    suffix = f"-attempt{i}" if len(paths) > 1 else ""
                    name = f"clouddump-{job_id}-{timestamp}{suffix}-{tag}.log"
                    attachments.append((sidecar, name))

    subject = f"[{status}] CloudDump {host}: {job_id}"
    if send_email(config, subject, body, attachments) is False:
        log.error("Job report email for %s could not be sent.", job_id)
