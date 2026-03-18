"""Entry point — run with ``python -m clouddump``."""

import logging
import os
import signal
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

import clouddump
from clouddump import cfg, redact, log, _safe_remove
from clouddump.config import load_config, validate_jobs, verify_connectivity
from clouddump.cron import should_run
from clouddump.email import send_email, send_job_report
from clouddump.jobs import execute_job


def _signal_handler(sig, _frame):
    """Handle SIGTERM/SIGINT: flag shutdown and terminate any running child process."""
    log.info("Received shutdown signal, exiting gracefully...")
    clouddump.shutdown_requested = True
    if clouddump.child_proc is not None:
        clouddump.child_proc.terminate()


def _run_now_handler(sig, _frame):
    """Handle SIGUSR1: run all jobs immediately on next loop iteration."""
    log.info("Received SIGUSR1, running all jobs now...")
    clouddump.run_now_requested = True


def _add_file_handler(path):
    """Add a DEBUG-level FileHandler to the logger. Returns the handler."""
    handler = logging.FileHandler(path, mode="a")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    return handler


def main():
    """Load config, validate, send startup email, run main loop."""
    version = clouddump.__version__
    log.info("CloudDump v%s Start", version)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGUSR1, _run_now_handler)

    config = load_config()
    host = cfg(config, "host")
    debug = cfg(config, "debug", False) is True

    clouddump.debug = debug
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info("CONFIGURATION:")
    log.info("Host: %s", host)

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

    smtp_server = cfg(config, "smtp_server")
    smtp_port = cfg(config, "smtp_port")
    mail_from = cfg(config, "mail_from")
    mail_to = cfg(config, "mail_to")

    startup_config = (
        f"Debug: {debug}\n"
        f"Email logs: {cfg(config, 'email_logs', False)}\n"
        f"SMTP server: {smtp_server}\n"
        f"SMTP port: {smtp_port}\n"
        f"Mail from: {mail_from}\n"
        f"Mail to: {mail_to}\n\n"
        f"Total jobs configured: {len(jobs)}"
    )
    startup_config = redact(startup_config)
    jobs_summary = redact(jobs_summary)

    for line in startup_config.splitlines():
        log.info("  %s", line)
    log.info("Jobs:")
    for line in jobs_summary.splitlines():
        log.info("  %s", line)

    verify_connectivity(jobs)

    startup_body = (
        f"CloudDump {host}\n\n"
        f"STARTED\n\n"
        f"CONFIGURATION\n\n"
        f"{startup_config}\n\n"
        f"JOBS\n\n"
        f"{jobs_summary}\n\n"
        f"CloudDump v{version}"
    )
    result = send_email(config, f"[Started] CloudDump {host}", startup_body)
    if result is True:
        log.info("Startup email sent.")
    elif result is None:
        log.info("Email not configured, skipping.")
    # result is False: send_email already logged the error

    # Main loop
    log.info("Starting main loop...")
    last_run = {}

    while not clouddump.shutdown_requested:
        force_run = clouddump.run_now_requested
        if force_run:
            clouddump.run_now_requested = False

        for job in jobs:
            if clouddump.shutdown_requested:
                break

            job_id = cfg(job, "id")
            crontab = cfg(job, "crontab")
            if not job_id or not crontab:
                continue

            if job_id not in last_run:
                last_run[job_id] = 0

            if not force_run and not should_run(crontab, last_run[job_id]):
                continue

            job_type = cfg(job, "type")
            log.info("Running job %s (type: %s)", job_id, job_type)

            timeout = int(cfg(job, "timeout", 604800))
            max_attempts = int(cfg(job, "retries", 3))

            for attempt in range(1, max_attempts + 1):
                fd, logfile_path = tempfile.mkstemp(prefix=f"clouddump-{job_id}-", suffix=".log")
                os.close(fd)

                t_start = time.time()
                clouddump.job_deadline = t_start + timeout

                file_handler = _add_file_handler(logfile_path)

                try:
                    log.debug("Job %s starting at %s",
                              job_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    result = execute_job(job, logfile_path)
                    t_end = time.time()
                    log.debug("Job %s finished at %s",
                              job_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                except Exception:
                    t_end = time.time()
                    tb = traceback.format_exc()
                    log.error("Job %s crashed:\n%s", job_id, tb)
                    result = 1
                finally:
                    clouddump.job_deadline = None
                    log.removeHandler(file_handler)
                    file_handler.close()

                if result == 0:
                    log.info("Job %s completed successfully", job_id)
                else:
                    log.warning("Job %s completed with errors (exit code: %d)", job_id, result)

                send_job_report(config, version, host, job, result, t_start, t_end, logfile_path,
                                attempt=attempt, max_attempts=max_attempts)

                _safe_remove(logfile_path)

                if result == 0:
                    break
                elif attempt < max_attempts:
                    log.warning("Job %s failed (attempt %d/%d), retrying in 60s...",
                             job_id, attempt, max_attempts)
                    time.sleep(60)
                else:
                    log.error("Job %s failed after %d attempts", job_id, max_attempts)

            last_run[job_id] = time.time()

        if clouddump.shutdown_requested:
            break

        # Touch heartbeat file so Docker HEALTHCHECK knows we're alive
        Path("/tmp/clouddump-heartbeat").touch()

        # Sleep until next minute boundary, waking every second to
        # check for SIGUSR1 (run now) or shutdown signals.
        sleep_seconds = 60 - datetime.now().second
        if sleep_seconds <= 0:
            sleep_seconds = 1
        try:
            for _ in range(sleep_seconds):
                if clouddump.shutdown_requested or clouddump.run_now_requested:
                    break
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            break


if __name__ == "__main__":
    main()
