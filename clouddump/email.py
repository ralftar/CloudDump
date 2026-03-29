"""Email reporting and notification."""

import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from clouddump import cfg, log, redact


def send_email(config, subject, body, attachments=None):
    """Send an email via SMTP with optional file attachments.

    Uses SMTP_SSL by default.  Set ``smtp_ssl`` to ``false`` in config to use
    plain SMTP (useful for relay servers that don't support SSL).

    Silently skips if smtp_server, smtp_port, or mail_to are not configured.
    Logs but does not raise on send failure.
    """
    smtp_server = cfg(config, "smtp_server")
    smtp_port = cfg(config, "smtp_port")
    smtp_user = cfg(config, "smtp_user")
    smtp_pass = cfg(config, "smtp_pass")
    smtp_ssl = cfg(config, "smtp_ssl", True)
    mail_from = cfg(config, "mail_from")
    mail_to = cfg(config, "mail_to")

    if not smtp_server or not smtp_port or not mail_to:
        return None  # Not configured

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

    msg = MIMEMultipart()
    msg["From"] = f"{mail_from} <{mail_from}>"
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    for entry in attachments or []:
        if isinstance(entry, tuple):
            path, name = entry
        else:
            path, name = entry, os.path.basename(entry)
        if os.path.isfile(path):
            with open(path, "r") as f:
                content = redact(f.read())
            part = MIMEApplication(content.encode("utf-8"), Name=name)
            part["Content-Disposition"] = f'attachment; filename="{name}"'
            msg.attach(part)

    log.info("Sending email to %s from %s.", ", ".join(recipients), mail_from)
    try:
        if smtp_ssl:
            srv = smtplib.SMTP_SSL(smtp_server, smtp_port)
        else:
            srv = smtplib.SMTP(smtp_server, smtp_port)
            if srv.has_extn("starttls"):
                srv.starttls()
        with srv:
            if smtp_user and smtp_pass:
                srv.login(smtp_user, smtp_pass)
            srv.sendmail(mail_from, recipients, msg.as_string())
    except Exception as exc:
        log.error("Failed to send email: %s", exc, exc_info=True)
        return False
    return True


def format_job_config(job):
    """Return a readable, redacted representation of a job's configuration."""
    return redact(json.dumps(job, indent=2, default=str))


def send_job_report(config, version, host, job, exit_code, t_start, t_end, logfile_path,
                    attempt=None, max_attempts=None):
    """Send job completion email, optionally with log attachment.

    When *attempt* and *max_attempts* are given, the subject and body include
    attempt information (e.g. ``[Failure - Attempt 1/3]``).

    Attaches the log file when ``email_log_attached`` is true in config.
    """
    email_log_attached = cfg(config, "email_log_attached", False)
    job_id = cfg(job, "id")
    job_type = cfg(job, "type")
    status = "Success" if exit_code == 0 else "Failure"
    elapsed = int(t_end - t_start)
    minutes, seconds = divmod(elapsed, 60)
    start_str = datetime.fromtimestamp(t_start, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_str = datetime.fromtimestamp(t_end, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    job_config_text = format_job_config(job)

    attempt_info = ""
    if attempt is not None and max_attempts is not None:
        attempt_info = f"Attempt: {attempt}/{max_attempts}\n"

    summary = f"{status} | {job_id} ({job_type}) | {minutes}m {seconds}s"
    if attempt is not None and max_attempts is not None:
        summary += f" | attempt {attempt}/{max_attempts}"
    log.info("Job report: %s", summary)

    body = (
        f"CloudDump {host}\n\n"
        f"JOB REPORT ({status})\n\n"
        f"Type: {job_type}\n"
        f"ID: {job_id}\n"
        f"{attempt_info}"
        f"Started: {start_str}\n"
        f"Completed: {end_str}\n"
        f"Time elapsed: {minutes} minutes {seconds} seconds\n\n"
        f"CONFIGURATION\n\n"
        f"{job_config_text}\n\n"
        f"{'See attached log for details.' if email_log_attached else 'Log available when email_log_attached is set to true.'}\n\n"
        f"CloudDump v{version}\n"
    )

    attachments = []
    if email_log_attached and os.path.isfile(logfile_path):
        timestamp = datetime.fromtimestamp(t_start, tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        log_attachment_name = f"clouddump-{job_id}-{timestamp}.log"
        attachments.append((logfile_path, log_attachment_name))

    if attempt is not None and max_attempts is not None:
        subject = f"[{status} - Attempt {attempt}/{max_attempts}] CloudDump {host}: {job_id}"
    else:
        subject = f"[{status}] CloudDump {host}: {job_id}"
    send_email(config, subject, body, attachments)
