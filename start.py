#!/usr/bin/env python3
"""Vendanor CloudDump - Backup orchestrator for S3, Azure Storage, and PostgreSQL."""

import copy
import json
import logging
import os
import re
import shutil
import signal
import smtplib
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

CONFIG_FILE = "/config/config.json"
VALID_JOB_TYPES = {"s3bucket", "azstorage", "pgsql"}
TOOL_REQUIREMENTS = {
    "s3bucket": ["aws"],
    "azstorage": ["azcopy"],
    "pgsql": ["pg_dump", "psql"],
}

child_proc = None
shutdown_requested = False

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

def read_version():
    try:
        return Path("/VERSION").read_text().strip().splitlines()[0]
    except Exception:
        return "unknown"


def load_config():
    if not os.path.isfile(CONFIG_FILE):
        log.error("Missing configuration file %s.", CONFIG_FILE)
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)


def cfg(d, key, default=""):
    """Get value from dict, treating None as *default*."""
    v = d.get(key)
    return default if v is None else v


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def redact(text):
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


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(settings, subject, body, attachments=None):
    smtp_server = cfg(settings, "SMTPSERVER")
    smtp_port = cfg(settings, "SMTPPORT")
    smtp_user = cfg(settings, "SMTPUSER")
    smtp_pass = cfg(settings, "SMTPPASS")
    mail_from = cfg(settings, "MAILFROM")
    mail_to = cfg(settings, "MAILTO")

    if not smtp_server or not smtp_port or not mail_to:
        log.warning("Email not configured, skipping.")
        return

    msg = MIMEMultipart()
    msg["From"] = f"{mail_from} <{mail_from}>"
    msg["To"] = mail_to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    for path in attachments or []:
        if os.path.isfile(path):
            with open(path, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(path))
            part["Content-Disposition"] = f'attachment; filename="{os.path.basename(path)}"'
            msg.attach(part)

    log.info("Sending email to %s from %s.", mail_to, mail_from)
    try:
        with smtplib.SMTP_SSL(smtp_server, int(smtp_port)) as srv:
            if smtp_user and smtp_pass:
                srv.login(smtp_user, smtp_pass)
            srv.sendmail(mail_from, mail_to, msg.as_string())
    except Exception as exc:
        log.error("Failed to send email: %s", exc)


# ---------------------------------------------------------------------------
# Mounts
# ---------------------------------------------------------------------------

def setup_mounts(settings):
    mounts = cfg(settings, "mount", [])
    summaries = []

    for m in mounts:
        path = cfg(m, "path").replace("\\", "/")
        mountpoint = cfg(m, "mountpoint")
        username = cfg(m, "username")
        privkey = cfg(m, "privkey")
        password = cfg(m, "password")
        port = cfg(m, "port")

        if not path or not mountpoint:
            continue

        summaries.append(f"Path: {path}\nMountpoint {mountpoint}")

        if ":" in path:  # SSH
            if privkey:
                ssh_dir = os.path.expanduser("~/.ssh")
                os.makedirs(ssh_dir, exist_ok=True)
                key_path = os.path.join(ssh_dir, "id_rsa")
                Path(key_path).write_text(privkey)
                os.chmod(key_path, 0o600)

            if "@" not in path and username:
                path = f"{username}@{path}"

            log.info("Mounting %s to %s using sshfs.", path, mountpoint)
            os.makedirs(mountpoint, exist_ok=True)

            cmd = ["sshfs", "-v", "-o", "StrictHostKeyChecking=no"]
            if port:
                cmd += ["-p", str(port)]
            cmd += [path, mountpoint]

            if run_cmd(cmd) != 0:
                log.error("Failed to mount %s to %s using sshfs.", path, mountpoint)
                sys.exit(1)
            log.info("Successfully mounted %s to %s.", path, mountpoint)

        elif path.startswith("//"):  # SMB
            stripped = path.lstrip("/")
            parts = stripped.split("/", 1)
            smb_host = parts[0]
            smb_share = parts[1] if len(parts) > 1 else ""

            log.info("Mounting %s to %s using smbnetfs.", path, mountpoint)
            smbnetfs_root = "/tmp/smbnetfs"

            if not os.path.isdir(os.path.join(smbnetfs_root, smb_host)):
                os.makedirs(smbnetfs_root, exist_ok=True)

                if username:
                    os.makedirs("/dev/shm", exist_ok=True)
                    cred_path = "/dev/shm/.smbcredentials"
                    cred = f"{username}\n{password}" if password else f"{username}\n"
                    Path(cred_path).write_text(cred)
                    os.chmod(cred_path, 0o600)

                    conf_path = "/dev/shm/smbnetfs.conf"
                    Path(conf_path).write_text(f"auth {cred_path}\n")

                    rc = run_cmd(["smbnetfs", smbnetfs_root, "-o", f"config={conf_path},allow_other"])
                else:
                    rc = run_cmd(["smbnetfs", smbnetfs_root, "-o", "allow_other"])

                if rc != 0:
                    log.error("Failed to mount smbnetfs at %s for %s.", smbnetfs_root, path)
                    sys.exit(1)
                time.sleep(2)

            src = os.path.join(smbnetfs_root, smb_host, smb_share)
            if os.path.islink(mountpoint):
                os.remove(mountpoint)
            os.symlink(src, mountpoint)
            log.info("Successfully mounted %s to %s.", path, mountpoint)

        else:
            log.error("Invalid path %s for mountpoint %s.", path, mountpoint)
            log.error('Syntax is "user@host:/path" for SSH, or "//host/path" for SMB.')
            sys.exit(1)

    return "\n\n".join(summaries)


# ---------------------------------------------------------------------------
# Job config formatting (for email reports)
# ---------------------------------------------------------------------------

def format_job_config(job):
    """Return a readable, redacted representation of a job's configuration."""
    j = copy.deepcopy(job)

    for bucket in j.get("buckets", []):
        bucket.pop("aws_access_key_id", None)
        bucket.pop("aws_secret_access_key", None)
    for server in j.get("servers", []):
        server.pop("pass", None)
    for bs in j.get("blobstorages", []):
        bs["source"] = bs.get("source", "").split("?")[0]

    lines = []
    for k, v in j.items():
        if k in ("blobstorages", "buckets", "servers"):
            lines.append(f"{k}:")
            for idx, item in enumerate(v):
                lines.append(f"  [{idx}]")
                for ik, iv in item.items():
                    lines.append(f"    {ik}: {iv}")
        else:
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subprocess wrapper (tracks child for signal forwarding)
# ---------------------------------------------------------------------------

def run_cmd(cmd, env=None, stdout=None, stderr=None):
    global child_proc
    proc = subprocess.Popen(cmd, env=env, stdout=stdout, stderr=stderr)
    child_proc = proc
    proc.wait()
    child_proc = None
    return proc.returncode


# ---------------------------------------------------------------------------
# S3 sync
# ---------------------------------------------------------------------------

def run_s3_sync(bucket, logfile):
    source = cfg(bucket, "source")
    destination = cfg(bucket, "destination")
    delete = cfg(bucket, "delete_destination", "true")
    key_id = cfg(bucket, "aws_access_key_id")
    secret = cfg(bucket, "aws_secret_access_key")
    region = cfg(bucket, "aws_region", "us-east-1")
    endpoint = cfg(bucket, "endpoint_url")

    if not source or not destination:
        logfile.write("ERROR: Missing source or destination for S3 bucket.\n")
        return 1

    if not source.startswith("s3://"):
        logfile.write(f"ERROR: Invalid source {source}. Must start with s3://\n")
        return 1

    if str(delete) not in ("true", "false"):
        delete = "true"

    os.makedirs(destination, exist_ok=True)

    logfile.write(f"Source: {source}\n")
    logfile.write(f"Destination: {destination}\n")
    logfile.write(f"Mirror (delete): {delete}\n")
    logfile.write(f"AWS Region: {region}\n")
    if endpoint:
        logfile.write(f"Endpoint URL: {endpoint}\n")
    logfile.write(f"Syncing source {source} to destination {destination}...\n")
    logfile.flush()

    env = {**os.environ}
    if key_id:
        env["AWS_ACCESS_KEY_ID"] = key_id
    if secret:
        env["AWS_SECRET_ACCESS_KEY"] = secret
    if region:
        env["AWS_DEFAULT_REGION"] = region

    cmd = ["aws", "s3", "sync"]
    if endpoint:
        cmd += ["--endpoint-url", endpoint]
    if str(delete) == "true":
        cmd.append("--delete")
    cmd += [source, destination]

    t0 = time.time()
    rc = run_cmd(cmd, env=env, stdout=logfile, stderr=logfile)
    elapsed = int(time.time() - t0)

    if rc != 0:
        logfile.write(f"ERROR: Sync failed after {elapsed} seconds.\n")
    else:
        logfile.write(f"Sync completed successfully in {elapsed} seconds.\n")
    return rc


# ---------------------------------------------------------------------------
# Azure sync
# ---------------------------------------------------------------------------

def run_az_sync(blobstorage, logfile):
    source = cfg(blobstorage, "source")
    destination = cfg(blobstorage, "destination")
    delete = cfg(blobstorage, "delete_destination", "true")

    if not source or not destination:
        logfile.write("ERROR: Missing source or destination for Azure blobstorage.\n")
        return 1

    if not source.startswith("https://"):
        logfile.write(f"ERROR: Invalid source. Must start with https://\n")
        return 1

    if str(delete) not in ("true", "false"):
        delete = "true"

    source_stripped = source.split("?")[0]
    os.makedirs(destination, exist_ok=True)

    logfile.write(f"Source: {source_stripped}\n")
    logfile.write(f"Destination: {destination}\n")
    logfile.write(f"Mirror (delete): {delete}\n")
    logfile.write(f"Syncing source {source_stripped} to destination {destination}...\n")
    logfile.flush()

    cmd = ["azcopy", "sync", "--recursive", f"--delete-destination={delete}", source, destination]

    t0 = time.time()
    rc = run_cmd(cmd, stdout=logfile, stderr=logfile)
    elapsed = int(time.time() - t0)

    if rc != 0:
        logfile.write(f"ERROR: Sync failed after {elapsed} seconds.\n")
    else:
        logfile.write(f"Sync completed successfully in {elapsed} seconds.\n")
    return rc


# ---------------------------------------------------------------------------
# PostgreSQL dump
# ---------------------------------------------------------------------------

def _list_databases(host, port, user, password):
    """Query the server for a list of databases using psql -l."""
    env = {**os.environ, "PGPASSWORD": password}
    result = subprocess.run(
        ["psql", "-h", host, "-p", str(port), "-U", user, "-l"],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        return None

    databases = []
    for line in result.stdout.splitlines():
        if "|" not in line:
            continue
        line = line.replace(" ", "")
        if line.startswith("Name|") or line.startswith("||"):
            continue
        name = line.split("|")[0]
        if name:
            databases.append(name)
    return databases


def run_pg_dump(server, logfile):
    host = cfg(server, "host")
    port = str(cfg(server, "port", "5432"))
    user = cfg(server, "user", "postgres")
    password = cfg(server, "pass")
    backuppath = cfg(server, "backuppath")
    filenamedate = str(cfg(server, "filenamedate", "false")).lower() == "true"
    compress = str(cfg(server, "compress", "true")).lower() == "true"
    databases_cfg = cfg(server, "databases", [])
    databases_excluded = cfg(server, "databases_excluded", [])

    if not host or not user or not password or not backuppath:
        logfile.write("ERROR: Missing required pgsql parameters.\n")
        return 1

    os.makedirs(backuppath, exist_ok=True)

    logfile.write(f"Host: {host}\n")
    logfile.write(f"Port: {port}\n")
    logfile.write(f"Username: {user}\n")
    logfile.write(f"Backup path: {backuppath}\n")
    logfile.write(f"Filename date: {filenamedate}\n")
    logfile.write(f"Compress: {compress}\n")

    logfile.write("Querying server for list of databases...\n")
    logfile.flush()

    all_dbs = _list_databases(host, port, user, password)
    if all_dbs is None:
        logfile.write(f"ERROR: Failed to query database list from {host}.\n")
        return 1

    # Determine which databases to back up
    configured_dbs = []
    db_table_configs = {}
    for entry in databases_cfg:
        if isinstance(entry, dict):
            for dbname, tbl_cfg in entry.items():
                configured_dbs.append(dbname)
                db_table_configs[dbname] = tbl_cfg or {}

    if configured_dbs:
        logfile.write(f"Using explicitly configured databases: {' '.join(configured_dbs)}\n")
        databases_to_backup = configured_dbs
    else:
        logfile.write("Using all databases except excluded ones\n")
        excluded_set = set(databases_excluded)
        databases_to_backup = [db for db in all_dbs if db not in excluded_set]

    if not databases_to_backup:
        logfile.write("ERROR: No databases to backup.\n")
        return 1

    logfile.write(f"Databases to backup: {' '.join(databases_to_backup)}\n")
    logfile.flush()

    env = {**os.environ, "PGPASSWORD": password}
    overall_result = 0

    for database in databases_to_backup:
        logfile.write(f"Processing database: {database}\n")

        tbl_cfg = db_table_configs.get(database, {})
        tables_included = tbl_cfg.get("tables_included", [])
        tables_excluded = tbl_cfg.get("tables_excluded", [])

        # Build pg_dump command
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        temp_file = os.path.join(backuppath, f"{database}-{timestamp}.tar")
        if filenamedate:
            final_file = temp_file
        else:
            final_file = os.path.join(backuppath, f"{database}.tar")

        cmd = ["pg_dump", "-h", host, "-p", port, "-U", user, "-d", database, "-F", "tar"]
        for t in tables_included:
            t = t.strip()
            if t:
                cmd += [f"--table={t}"]
        for t in tables_excluded:
            t = t.strip()
            if t:
                cmd += [f"--exclude-table={t}"]

        logfile.write(f"Running pg_dump of {database} for {host} to backupfile {final_file}...\n")
        logfile.flush()

        with open(temp_file, "wb") as dump_out:
            rc = run_cmd(cmd, env=env, stdout=dump_out, stderr=logfile)

        if rc != 0:
            logfile.write(f"ERROR: pg_dump for {database} on {host} failed.\n")
            _safe_remove(temp_file)
            overall_result = 1
            continue

        if not os.path.isfile(temp_file):
            logfile.write(f"ERROR: Backupfile {temp_file} missing for {database}.\n")
            overall_result = 1
            continue

        size = os.path.getsize(temp_file)
        if size == 0:
            logfile.write(f"ERROR: Backupfile {temp_file} is empty.\n")
            _safe_remove(temp_file)
            overall_result = 1
            continue

        logfile.write(f"pg_dump of {database} completed. Backupfile size: {size} bytes.\n")

        if compress:
            logfile.write(f"Compressing backupfile {temp_file}...\n")
            logfile.flush()
            rc = run_cmd(["bzip2", "-f", temp_file])
            if rc != 0:
                logfile.write(f"ERROR: Compression of {temp_file} failed.\n")
                overall_result = 1
                continue
            temp_file += ".bz2"
            if filenamedate:
                final_file += ".bz2"
            else:
                final_file = os.path.join(backuppath, f"{database}.tar.bz2")
            logfile.write(f"Compression completed. Compressed file: {temp_file}\n")

        if temp_file != final_file:
            logfile.write(f"Moving {temp_file} to {final_file}...\n")
            try:
                shutil.move(temp_file, final_file)
            except OSError as exc:
                logfile.write(f"ERROR: Could not move {temp_file} to {final_file}: {exc}\n")
                overall_result = 1
                continue

        logfile.write(f"Backup completed successfully: {final_file}\n")

    if overall_result == 0:
        logfile.write("All database backups completed successfully.\n")
    else:
        logfile.write("Some database backups failed.\n")
    return overall_result


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------

def _matches_field(pattern, value):
    if pattern == "*":
        return True
    if pattern.startswith("*/"):
        step = int(pattern[2:])
        return value % step == 0
    return int(pattern) == value


def matches_cron(cron_pattern, dt):
    """Check if a datetime matches a 5-field cron pattern."""
    fields = cron_pattern.split()
    if len(fields) != 5:
        return False

    minute, hour, day, month, dow = (dt.minute, dt.hour, dt.day, dt.month,
                                     (dt.weekday() + 1) % 7)

    return (
        _matches_field(fields[0], minute)
        and _matches_field(fields[1], hour)
        and _matches_field(fields[2], day)
        and _matches_field(fields[3], month)
        and _matches_field(fields[4], dow)
    )


def should_run(cron_pattern, last_run_ts):
    """Determine if a job should run, implementing catch-up execution."""
    now = time.time()

    if last_run_ts == 0:
        return matches_cron(cron_pattern, datetime.fromtimestamp(now))

    # Align to minute boundaries
    last_minute = int(last_run_ts) // 60 * 60
    current_minute = int(now) // 60 * 60

    ts = last_minute + 60
    while ts <= current_minute:
        if matches_cron(cron_pattern, datetime.fromtimestamp(ts)):
            return True
        ts += 60

    return False


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _signal_handler(sig, _frame):
    global shutdown_requested
    log.info("Received shutdown signal, exiting gracefully...")
    shutdown_requested = True
    if child_proc is not None:
        child_proc.terminate()


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

def _collect_azcopy_logs(logfile_path):
    """Find azcopy log file paths referenced in a job log."""
    paths = []
    try:
        with open(logfile_path) as f:
            for line in f:
                if line.startswith("Log file is located at: ") and line.rstrip().endswith(".log"):
                    p = line.split("Log file is located at: ", 1)[1].strip()
                    if os.path.isfile(p):
                        paths.append(p)
    except OSError:
        pass
    return paths


def execute_job(job, logfile_path, logfile):
    """Run a single job; return exit code."""
    job_type = cfg(job, "type")

    if job_type == "s3bucket":
        buckets = cfg(job, "buckets", [])
        if not buckets:
            logfile.write("ERROR: No buckets configured.\n")
            return 1
        rc = 0
        for bucket in buckets:
            r = run_s3_sync(bucket, logfile)
            if r != 0:
                rc = r
        return rc

    elif job_type == "azstorage":
        blobstorages = cfg(job, "blobstorages", [])
        if not blobstorages:
            logfile.write("ERROR: No blobstorages configured.\n")
            return 1
        rc = 0
        for bs in blobstorages:
            r = run_az_sync(bs, logfile)
            if r != 0:
                rc = r
        return rc

    elif job_type == "pgsql":
        servers = cfg(job, "servers", [])
        if not servers:
            logfile.write("ERROR: No servers configured.\n")
            return 1
        rc = 0
        for server in servers:
            r = run_pg_dump(server, logfile)
            if r != 0:
                rc = r
        return rc

    logfile.write(f"ERROR: Unknown job type {job_type}.\n")
    return 1


def send_job_report(settings, version, host, job, exit_code, t_start, t_end, logfile_path):
    """Send job completion email with log attachments."""
    job_id = cfg(job, "id")
    job_type = cfg(job, "type")
    status = "Success" if exit_code == 0 else "Failure"
    elapsed = int(t_end - t_start)
    minutes, seconds = divmod(elapsed, 60)
    start_str = datetime.fromtimestamp(t_start).strftime("%Y-%m-%d %H:%M:%S")
    end_str = datetime.fromtimestamp(t_end).strftime("%Y-%m-%d %H:%M:%S")

    job_config_text = format_job_config(job)

    body = (
        f"CloudDump {host}\n\n"
        f"JOB REPORT ({status})\n\n"
        f"Type: {job_type}\n"
        f"ID: {job_id}\n"
        f"Started: {start_str}\n"
        f"Completed: {end_str}\n"
        f"Time elapsed: {minutes} minutes {seconds} seconds\n\n"
        f"CONFIGURATION\n\n"
        f"{job_config_text}\n\n"
        f"For more information consult the attached logs.\n\n"
        f"Vendanor CloudDump v{version}\n"
    )

    attachments = [logfile_path]
    azcopy_logs = _collect_azcopy_logs(logfile_path)
    attachments.extend(azcopy_logs)

    subject = f"[{status}] CloudDump {host}: {job_id}"
    send_email(settings, subject, body, attachments)

    for p in azcopy_logs:
        _safe_remove(p)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_jobs(jobs):
    errors = 0
    seen_ids = {}
    summaries = []

    for i, job in enumerate(jobs):
        job_id = cfg(job, "id")
        if not job_id:
            log.error("Missing job ID for job index %d.", i)
            errors += 1
            continue

        if job_id in seen_ids:
            log.error("Duplicate job ID '%s' at index %d (first at %d).", job_id, i, seen_ids[job_id])
            errors += 1
        seen_ids[job_id] = i

        job_type = cfg(job, "type")
        if not job_type:
            log.error("Missing type for job ID %s.", job_id)
            errors += 1
            continue
        if job_type not in VALID_JOB_TYPES:
            log.error("Invalid job type '%s' for job ID %s. Must be one of: %s.",
                       job_type, job_id, ", ".join(sorted(VALID_JOB_TYPES)))
            errors += 1
            continue

        crontab = cfg(job, "crontab")
        if not crontab:
            log.error("Missing crontab for job ID %s.", job_id)
            errors += 1
            continue
        if len(crontab.split()) != 5:
            log.error("Invalid crontab '%s' for job ID %s: expected 5 fields.", crontab, job_id)
            errors += 1

        for tool in TOOL_REQUIREMENTS.get(job_type, []):
            if not shutil.which(tool):
                log.error("Required tool '%s' not found for job ID %s (type: %s).", tool, job_id, job_type)
                errors += 1

        debug = cfg(job, "debug", False)
        summaries.append(f"ID: {job_id}\nType: {job_type}\nSchedule: {crontab}\nDebug: {debug}")

    return errors, "\n\n".join(summaries)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global shutdown_requested

    version = read_version()
    log.info("Vendanor CloudDump v%s Start", version)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    config = load_config()
    settings = config.get("settings", {})
    host = cfg(settings, "HOST")
    debug = str(cfg(settings, "DEBUG", "false")).lower() == "true"

    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info("CONFIGURATION:")
    log.info("Host: %s", host)

    # Set up mounts
    mounts_summary = setup_mounts(settings)

    # Validate jobs
    jobs = config.get("jobs", [])
    if not jobs:
        log.error("No jobs in configuration.")
        sys.exit(1)

    errors, jobs_summary = validate_jobs(jobs)
    if errors:
        log.error("Configuration validation failed with %d error(s). Exiting.", errors)
        sys.exit(1)
    log.info("All %d job(s) passed configuration validation.", len(jobs))

    # Build and send startup email
    smtp_server = cfg(settings, "SMTPSERVER")
    smtp_port = cfg(settings, "SMTPPORT")
    mail_from = cfg(settings, "MAILFROM")
    mail_to = cfg(settings, "MAILTO")

    startup_config = (
        f"Debug: {debug}\n"
        f"SMTP server: {smtp_server}\n"
        f"SMTP port: {smtp_port}\n"
        f"Mail from: {mail_from}\n"
        f"Mail to: {mail_to}"
    )
    if mounts_summary:
        startup_config += f"\n\nMountpoints:\n{mounts_summary}"
    startup_config += f"\n\nTotal jobs configured: {len(jobs)}"
    startup_config = redact(startup_config)
    jobs_summary = redact(jobs_summary)

    startup_body = (
        f"CloudDump {host}\n\n"
        f"STARTED\n\n"
        f"CONFIGURATION\n\n"
        f"{startup_config}\n\n"
        f"JOBS\n\n"
        f"{jobs_summary}\n\n"
        f"Vendanor CloudDump v{version}"
    )
    send_email(settings, f"[Started] CloudDump {host}", startup_body)
    log.info("Startup email sent.")

    # Main loop
    log.info("Starting main loop...")
    last_run = {}

    while not shutdown_requested:
        for job in jobs:
            if shutdown_requested:
                break

            job_id = cfg(job, "id")
            crontab = cfg(job, "crontab")
            if not job_id or not crontab:
                continue

            if job_id not in last_run:
                last_run[job_id] = 0

            if not should_run(crontab, last_run[job_id]):
                continue

            job_type = cfg(job, "type")
            log.info("Running job %s (type: %s)", job_id, job_type)

            fd, logfile_path = tempfile.mkstemp(prefix=f"vnclouddump-{job_id}-", suffix=".log")
            os.close(fd)

            t_start = time.time()
            start_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with open(logfile_path, "w") as logfile:
                logfile.write(f"Job {job_id} starting at {start_str}\n")
                result = execute_job(job, logfile_path, logfile)
                t_end = time.time()
                logfile.write(f"Job {job_id} finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

            if result == 0:
                log.info("Job %s completed successfully", job_id)
            else:
                log.info("Job %s completed with errors (exit code: %d)", job_id, result)

            send_job_report(settings, version, host, job, result, t_start, t_end, logfile_path)
            _safe_remove(logfile_path)
            last_run[job_id] = time.time()

        if shutdown_requested:
            break

        # Sleep until next minute boundary
        sleep_seconds = 60 - datetime.now().second
        if sleep_seconds <= 0:
            sleep_seconds = 1
        try:
            time.sleep(sleep_seconds)
        except (KeyboardInterrupt, SystemExit):
            break


if __name__ == "__main__":
    main()
