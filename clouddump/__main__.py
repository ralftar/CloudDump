"""Entry point — run with ``python -m clouddump``."""

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone

import clouddump
from clouddump import cfg, net_bytes, redact, log, _safe_remove
from clouddump.config import load_config, validate_settings, validate_jobs, verify_connectivity
from clouddump.cron import should_run
from clouddump.email import send_email, send_job_report
from clouddump.health import start_health_server, update_last_run, update_job_metric
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


def _tool_versions():
    """Collect version strings for installed backup tools."""
    tools = [
        ("pg_dump", ["pg_dump", "--version"]),
        ("mysqldump", ["mysqldump", "--version"]),
        ("aws", ["aws", "--version"]),
        ("azcopy", ["azcopy", "--version"]),
        ("rsync", ["rsync", "--version"]),
        ("git", ["git", "--version"]),
    ]
    versions = []
    for name, cmd in tools:
        if not shutil.which(cmd[0]):
            continue
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=10)
            version_line = out.decode("utf-8", errors="replace").strip().splitlines()[0]
            versions.append(f"  {name}: {version_line}")
        except Exception:
            versions.append(f"  {name}: installed (version unknown)")
    return "\n".join(versions)


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

    tool_versions = _tool_versions()

    log.info("Configuration:\n%s", startup_config)
    log.info("Tools:\n%s", tool_versions)
    log.info("Jobs:\n%s", jobs_summary)

    verify_connectivity(jobs)

    startup_body = (
        f"CloudDump started!\n\n"
        f"CONFIGURATION\n\n"
        f"{startup_config}\n\n"
        f"TOOLS\n\n"
        f"{tool_versions}\n\n"
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
                log.info("Starting job", extra={"job_type": job_type})

                timeout = int(cfg(job, "timeout", 604800))
                max_attempts = int(cfg(job, "retries", 3))

                job_t_start = time.time()
                logfile_paths = []
                total_rx = 0
                total_tx = 0
                final_attempt = 0

                for attempt in range(1, max_attempts + 1):
                    final_attempt = attempt
                    fd, logfile_path = tempfile.mkstemp(prefix=f"clouddump-{job_id}-", suffix=".log")
                    os.close(fd)
                    logfile_paths.append(logfile_path)

                    t_start = time.time()
                    clouddump.job_deadline = t_start + timeout

                    file_handler = _add_file_handler(logfile_path)
                    log.info("Starting attempt", extra={"attempt": attempt, "max_attempts": max_attempts})

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
                    if net_before and net_after:
                        total_rx += net_after[0] - net_before[0]
                        total_tx += net_after[1] - net_before[1]

                    rx_bytes = (net_after[0] - net_before[0]) if net_before and net_after else None
                    tx_bytes = (net_after[1] - net_before[1]) if net_before and net_after else None
                    metric_status = "success" if result == 0 else "failure"
                    update_job_metric(job_id, job_type, metric_status, elapsed, rx=rx_bytes, tx=tx_bytes)

                    extras = {"status": metric_status, "elapsed_s": elapsed,
                              "attempt": attempt, "max_attempts": max_attempts}
                    if rx_bytes is not None:
                        extras["rx_bytes"] = rx_bytes
                    if tx_bytes is not None:
                        extras["tx_bytes"] = tx_bytes

                    if result == 0:
                        log.info("Attempt completed successfully", extra=extras)
                        break
                    else:
                        extras["exit_code"] = result
                        log.warning("Attempt completed with errors", extra=extras)
                        if attempt < max_attempts:
                            log.warning("Retrying in 60s...")
                            time.sleep(60)
                        else:
                            log.error("Failed after all attempts",
                                      extra={"max_attempts": max_attempts})

                # Determine three-tier status and send one email
                job_t_end = time.time()
                if result == 0 and final_attempt == 1:
                    job_status = "Success"
                elif result == 0:
                    job_status = "Warning"
                else:
                    job_status = "Failure"

                send_job_report(config, version, host, job, result,
                                job_t_start, job_t_end, logfile_paths,
                                status=job_status, attempts_used=final_attempt,
                                max_attempts=max_attempts)

                for lf in logfile_paths:
                    _safe_remove(lf)

                if result == 0:
                    succeeded += 1
                else:
                    failed += 1

                clouddump.current_job = ""

            if not clouddump.shutdown_requested:
                run_end = datetime.now(timezone.utc)
                run_elapsed = int((run_end - run_start).total_seconds())
                run_min, run_sec = divmod(run_elapsed, 60)
                run_extras = {"succeeded": succeeded, "failed": failed,
                              "total": len(jobs), "elapsed_s": run_elapsed}
                if failed:
                    log.warning("Jobs completed with failures", extra=run_extras)
                else:
                    log.info("All jobs completed", extra=run_extras)
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
