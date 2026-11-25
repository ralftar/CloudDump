"""Email reporting and notification."""

import gzip
import json
import os
import smtplib
from html import escape
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from clouddump import cfg, fmt_bytes, log, redact
from clouddump import report


DEFAULT_EMAIL_SIZE_LIMIT_MB = 15

# A hung SMTP server must not stall the main loop. Every other blocking call in
# CloudDump is deadline-bound (run_cmd, _run_verify, PGCONNECT_TIMEOUT); this
# closes the last gap. Generous enough for a large attachment over a slow link.
SMTP_TIMEOUT_SECONDS = 60


def _resolve_smtp_security(config):
    """Determine SMTP security mode from config.

    Supports ``smtp_security`` field (``"ssl"``, ``"starttls"``,
    ``"none"``).  Default is ``"ssl"``.
    """
    return cfg(config, "smtp_security") or "ssl"


def _html_part(body, html):
    """The plain text and the HTML as alternatives, with the logo the HTML shows.

    multipart/related wraps multipart/alternative and the logo, so a client
    that shows the HTML finds ``cid:logo`` and one that shows the text never
    lists the logo as an attachment.
    """
    related = MIMEMultipart("related")
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(body, "plain", "utf-8"))
    alternative.attach(MIMEText(html, "html", "utf-8"))
    related.attach(alternative)
    with open(report.LOGO_PATH, "rb") as f:
        logo = MIMEImage(f.read(), "png")
    logo["Content-ID"] = f"<{report.LOGO_CID}>"
    logo["Content-Disposition"] = 'inline; filename="logo.png"'
    related.attach(logo)
    return related


def _build_message(mail_from, recipients, subject, body, parts, html=None):
    """Assemble a MIMEMultipart message from prepared attachment parts.

    *parts* is a list of ``(filename, payload_bytes)`` tuples; each is
    attached as a base64-encoded MIMEApplication. With *html*, the body goes
    out as HTML with *body* as its plain-text alternative.
    """
    msg = MIMEMultipart()
    msg["From"] = mail_from
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(_html_part(body, html) if html else MIMEText(body, "plain", "utf-8"))
    for filename, payload in parts:
        part = MIMEApplication(payload, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)
    return msg


def _enforce_size_limit(config, mail_from, recipients, subject, body, raw_parts, html=None):
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

    msg = _build_message(mail_from, recipients, subject, body, raw_parts, html)
    if not raw_parts or len(msg.as_bytes()) <= limit:
        return msg

    # Stage 1: compress each attachment individually.
    gz_parts = [(f"{name}.gz", gzip.compress(payload)) for name, payload in raw_parts]
    msg = _build_message(mail_from, recipients, subject, body, gz_parts, html)
    if len(msg.as_bytes()) <= limit:
        log.info("Email attachments exceeded %d MB; compressed to fit.", limit_mb)
        return msg

    # Stage 2: still too large — drop attachments, but make the omission
    # explicit in the body so the report never hides that a log is missing.
    log.error(
        "Email attachments exceed the %d MB limit even after compression; "
        "sending report without attachments.", limit_mb,
    )
    warning = (
        f"WARNING: log attachment(s) omitted — exceeded the {limit_mb} MB email "
        f"size limit even after compression. Retrieve the full log from the "
        f"container/host logs."
    )
    if html:
        html = html.replace(report.NOTICE_MARK, report.section(
            "Logs omitted", report.pre(warning, "error")), 1)
    notice = f"{warning}\n\n{'-' * 60}\n\n"
    return _build_message(mail_from, recipients, subject, notice + body, [], html)


def send_email(config, subject, body, attachments=None, html=None):
    """Send an email via SMTP with optional file attachments.

    Encryption modes (``smtp_security``):

    - ``"ssl"`` (default) — SMTP_SSL, typically port 465.
    - ``"starttls"`` — STARTTLS upgrade, typically port 587.
    - ``"none"`` — plain SMTP, no encryption (local relays only).

    With *html*, the message is HTML with *body* as its plain-text alternative.

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

    msg = _enforce_size_limit(config, mail_from, recipients, subject, body, raw_parts, html)

    log.info("Sending email to %s from %s.", ", ".join(recipients), mail_from)
    try:
        if security == "ssl":
            srv = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=SMTP_TIMEOUT_SECONDS)
        else:
            srv = smtplib.SMTP(smtp_server, smtp_port, timeout=SMTP_TIMEOUT_SECONDS)
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


# What a job's targets are called, for "1 of 2 accounts failed".
_TARGET_NOUNS = {
    "imap": "account",
    "pgsql": "server",
    "github": "organization",
    "rsync": "target",
    "azstorage": "storage account",
}


def _duration(seconds):
    """``2h 16m``, ``2m 48s``, ``12s``."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


def _utc(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _read_logs(paths):
    texts = []
    for path in paths:
        if os.path.isfile(path):
            with open(path, "r", errors="replace") as f:
                texts.append(redact(f.read()))
    return texts


def send_job_report(config, version, host, job, exit_code, t_start, t_end, logfile_paths,
                    status=None, attempts_used=None, max_attempts=None, outcomes=None):
    """Send the job completion email, optionally with log attachments.

    *logfile_paths* is a list of log files (one per attempt).  All are
    attached when ``email_log_attached`` is true in config.

    *status* is one of ``"Success"``, ``"Warning"``, or ``"Failure"``.

    *outcomes* holds one ``(target, label, exit_code, attempt)`` per target:
    its latest result and the attempt that produced it. The email leads with
    them and with the error lines from the logs, so what failed and why is in
    the message, not only in an attachment.
    """
    email_log_attached = cfg(config, "email_log_attached", False)
    job_id = cfg(job, "id")
    job_type = cfg(job, "type")
    if status is None:
        status = "Success" if exit_code == 0 else "Failure"
    outcomes = outcomes or []
    paths = logfile_paths if isinstance(logfile_paths, list) else [logfile_paths]
    elapsed = _duration(t_end - t_start)
    attempts = (f"{attempts_used} of {max_attempts}"
                if attempts_used is not None and max_attempts is not None else None)

    summary = f"{status} | {job_id} ({job_type}) | {elapsed}"
    if attempts:
        summary += f" | attempt {attempts_used}/{max_attempts}"
    log.info("Job report: %s", summary)

    noun = _TARGET_NOUNS.get(job_type, "target")
    failed = [o for o in outcomes if o[2] != 0]
    if status == "Failure" and len(outcomes) > 1:
        headline = f"{len(failed)} of {len(outcomes)} {noun}s failed"
    elif status == "Failure":
        headline = f"The {noun} failed" if outcomes else "The job failed"
    elif status == "Warning":
        headline = f"Succeeded on attempt {attempts}"
    else:
        headline = f"All {len(outcomes)} {noun}s backed up" if len(outcomes) > 1 else "Backed up"
    if status == "Failure" and attempts_used and attempts_used > 1:
        headline += f" after {attempts_used} attempts"
    headline += f" · {elapsed}"

    # Errors matter when the job did not pass first time: for a Warning they
    # are what the retry got past.
    errors, more = ([], 0) if status == "Success" else report.error_lines(_read_logs(paths))

    rows = [("Job", f"{job_id} ({job_type})"), ("Started", _utc(t_start)),
            ("Finished", _utc(t_end)), ("Duration", elapsed)]
    if attempts:
        rows.append(("Attempts", attempts))

    sections = [report.section("Summary", report.facts(rows))]
    if errors:
        title = "What went wrong" if status == "Failure" else "Errors before the retry succeeded"
        text = "\n".join(errors) + (f"\n… and {more} more in the log" if more else "")
        sections.append(report.section(title, report.pre(text, "error")))
    if outcomes:
        # Not every job type has a destination per target (pgsql, github).
        dest = any(cfg(t, "destination") for t, _, _, _ in outcomes)
        sections.append(report.section(f"{noun}s", report.table(
            ["", noun.capitalize()] + (["Destination"] if dest else []) + ["Attempt"],
            [[report.badge(rc == 0), escape(label)]
             + ([escape(cfg(t, "destination"))] if dest else []) + [str(attempt)]
             for t, label, rc, attempt in outcomes])))
    sections.append(report.section("Configuration", report.pre(format_job_config(job))))
    sections.append(report.section("Logs", report.note(
        "The full log of each attempt is attached." if email_log_attached
        else "Set email_log_attached to true to get the logs attached.")))

    html = report.page(status, job_id, headline, host, version, sections)

    lines = [f"CloudDump {host}: {job_id} — {status}", headline, ""]
    lines += [f"{label}: {value}" for label, value in rows]
    if errors:
        lines += ["", "WHAT WENT WRONG" if status == "Failure" else "ERRORS BEFORE THE RETRY SUCCEEDED", ""]
        lines += errors + ([f"… and {more} more in the log"] if more else [])
    if outcomes:
        lines += ["", f"{noun.upper()}S", ""]
        for t, label, rc, attempt in outcomes:
            dest = cfg(t, "destination")
            lines.append(f"{'OK  ' if rc == 0 else 'FAIL'}  {label}"
                         + (f" -> {dest}" if dest else "") + f"  (attempt {attempt})")
    lines += ["", "CONFIGURATION", "", format_job_config(job), "",
              "See attached log(s) for details." if email_log_attached
              else "Logs available when email_log_attached is set to true.",
              "", "----", f"CloudDump v{version}", report.REPO_URL, ""]
    body = "\n".join(lines)

    attachments = []
    if email_log_attached:
        import glob
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
    if failed and len(outcomes) > 1:
        subject += f" ({len(failed)} of {len(outcomes)} failed)"
    if send_email(config, subject, body, attachments, html) is False:
        log.error("Job report email for %s could not be sent.", job_id)


def send_startup_report(config, version, host, startup_config, jobs_summary,
                        connectivity, tool_versions):
    """Send the startup email: schedule, connectivity, jobs, tools, settings.

    *connectivity* is a list of ``OK: ...`` / ``WARN: ...`` lines; any WARN
    makes it "Degraded". Returns send_email's result.
    """
    warns = sum(1 for line in connectivity if line.startswith("WARN:"))
    status = "Degraded" if warns else "Started"
    if warns:
        headline = f"{warns} of {len(connectivity)} endpoints unreachable"
    elif connectivity:
        headline = f"All {len(connectivity)} endpoints reachable"
    else:
        headline = "Waiting for the schedule"

    checks = [[report.badge(not line.startswith("WARN:")), escape(line.split(":", 1)[-1].strip())]
              for line in connectivity]
    tools = [line[2:].split(": ", 1) for line in tool_versions.splitlines() if line.startswith("- ")]
    sections = [report.section("Schedule", report.facts(
        [("Crontab", cfg(config, "crontab")), ("Version", f"v{version}")]))]
    if checks:
        sections.append(report.section("Connectivity", report.table(["", "Endpoint"], checks)))
    sections.append(report.section("Jobs", report.pre(jobs_summary)))
    if tools:
        sections.append(report.section("Tools", report.facts([(t[0], t[-1]) for t in tools])))
    sections.append(report.section("Configuration", report.pre(startup_config)))
    html = report.page(status, "CloudDump started", headline, host, version, sections)

    connectivity_text = "\n".join(connectivity) if connectivity else "No endpoints to check."
    body = (
        f"CloudDump at {host} has started — {headline}.\n\n"
        f"CONFIGURATION\n\n{startup_config}\n\n"
        f"JOBS\n\n{jobs_summary}\n\n"
        f"CONNECTIVITY\n\n{connectivity_text}\n\n"
        f"TOOLS\n\n{tool_versions}\n\n"
        f"----\nCloudDump v{version}\n{report.REPO_URL}"
    )
    return send_email(config, f"[{status}] CloudDump {host}", body, html=html)
