"""Entry point — run with ``python -m clouddump``."""

import json
import logging
import os
import signal
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone

import clouddump
from clouddump import cfg, fmt_bytes, net_bytes, redact, log, _safe_remove
from clouddump.config import load_config, validate_settings, validate_jobs, verify_connectivity
from clouddump.cron import should_run
from clouddump.email import send_email, send_job_report
from clouddump.health import start_health_server, update_last_run
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
    if not host:
        log.error("Missing required top-level 'host' in configuration.")
        sys.exit(1)
    settings_errors = validate_settings(config)
    debug = cfg(config, "debug", False)

    clouddump.debug = debug
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    crontab = cfg(config, "crontab")
    if not crontab:
        log.error("Missing top-level 'crontab' in configuration.")
        sys.exit(1)

    # Validate jobs
    jobs = config.get("jobs", [])
    if not jobs:
        log.error("No jobs in configuration.")
        sys.exit(1)

    errors, jobs_summary = validate_jobs(jobs)
    errors += settings_errors
    if errors:
        log.error("Configuration validation failed with %d error(s). Exiting.", errors)
        sys.exit(1)
    log.info("All %d job(s) passed configuration validation.", len(jobs))

    # Build a settings-only view (exclude jobs — logged separately)
    settings = {k: v for k, v in config.items() if k != "jobs"}
    startup_config = redact(json.dumps(settings, indent=2, default=str))
    jobs_summary = redact(jobs_summary)

    log.info("Configuration:\n%s", startup_config)
    log.info("Jobs:\n%s", jobs_summary)

    verify_connectivity(jobs)

    startup_body = (
        f"CloudDump started!\n\n"
        f"CONFIGURATION\n\n"
        f"{startup_config}\n\n"
        f"JOBS\n\n"
        f"{jobs_summary}\n\n"
        f"----\n"
        f"CloudDump v{version}\n"
        f"https://github.com/ralftar/CloudDump"
    )
    result = send_email(config, f"[Started] CloudDump {host}", startup_body)
    if result is True:
        log.info("Startup email sent.")
    elif result is None:
        log.info("Email not configured, skipping.")
    # result is False: send_email already logged the error

    start_health_server(int(cfg(config, "health_port", 8080)))

    # Main loop
    log.info("Starting main loop...")
    last_run_ts = 0

    while not clouddump.shutdown_requested:
        force_run = clouddump.run_now_requested
        if force_run:
            clouddump.run_now_requested = False

        if not force_run and not should_run(crontab, last_run_ts):
            pass  # Not time yet — skip to sleep
        else:
            log.info("Schedule triggered, running all jobs...")
            last_run_ts = time.time()
            run_start = datetime.now(timezone.utc)
            succeeded = 0
            failed = 0

            for job in jobs:
                if clouddump.shutdown_requested:
                    break

                job_id = cfg(job, "id")
                if not job_id:
                    continue

                job_type = cfg(job, "type")
                clouddump.current_job = job_id
                log.info("Starting (type: %s)", job_type)

                timeout = int(cfg(job, "timeout", 604800))
                max_attempts = int(cfg(job, "retries", 3))

                for attempt in range(1, max_attempts + 1):
                    fd, logfile_path = tempfile.mkstemp(prefix=f"clouddump-{job_id}-", suffix=".log")
                    os.close(fd)

                    t_start = time.time()
                    clouddump.job_deadline = t_start + timeout

                    file_handler = _add_file_handler(logfile_path)

                    net_before = net_bytes()

                    try:
                        result = execute_job(job, logfile_path)
                        t_end = time.time()
                    except Exception:
                        t_end = time.time()
                        tb = traceback.format_exc()
                        log.error("Crashed:\n%s", tb)
                        result = 1
                    finally:
                        clouddump.job_deadline = None
                        log.removeHandler(file_handler)
                        file_handler.close()

                    elapsed = int(t_end - t_start)
                    minutes, seconds = divmod(elapsed, 60)

                    net_after = net_bytes()
                    net_info = ""
                    if net_before and net_after:
                        rx = net_after[0] - net_before[0]
                        tx = net_after[1] - net_before[1]
                        net_info = f", rx {fmt_bytes(rx)}, tx {fmt_bytes(tx)}"

                    if result == 0:
                        log.info("Completed successfully (%dm %ds%s)", minutes, seconds, net_info)
                    else:
                        log.warning("Completed with errors (exit code: %d, %dm %ds%s)",
                                    result, minutes, seconds, net_info)

                    send_job_report(config, version, host, job, result, t_start, t_end, logfile_path,
                                    attempt=attempt, max_attempts=max_attempts)

                    _safe_remove(logfile_path)

                    if result == 0:
                        succeeded += 1
                        break
                    elif attempt < max_attempts:
                        log.warning("Failed (attempt %d/%d), retrying in 60s...",
                                    attempt, max_attempts)
                        time.sleep(60)
                    else:
                        failed += 1
                        log.error("Failed after %d attempts", max_attempts)

                clouddump.current_job = ""

            if not clouddump.shutdown_requested:
                run_end = datetime.now(timezone.utc)
                run_elapsed = int((run_end - run_start).total_seconds())
                run_min, run_sec = divmod(run_elapsed, 60)
                if failed:
                    log.warning("Jobs completed: %d succeeded, %d failed, %d total (%dm %ds)",
                                succeeded, failed, len(jobs), run_min, run_sec)
                else:
                    log.info("Jobs completed: %d/%d succeeded (%dm %ds)",
                             succeeded, len(jobs), run_min, run_sec)
                update_last_run(
                    started=run_start,
                    finished=run_end,
                    succeeded=succeeded,
                    failed=failed,
                    total=len(jobs),
                )

        if clouddump.shutdown_requested:
            break

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
