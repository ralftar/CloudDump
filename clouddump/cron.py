"""Cron schedule evaluation using croniter for full cron syntax support."""

import time
from datetime import datetime

from croniter import croniter


def validate_cron(cron_pattern):
    """Validate a 5-field cron pattern. Returns an error string, or None if valid.

    Supports standard cron syntax: ``*``, ``*/N``, exact integers, ranges
    (``1-5``), and lists (``1,3,5``).
    """
    fields = cron_pattern.split()
    if len(fields) != 5:
        return f"expected 5 fields, got {len(fields)}"
    try:
        croniter(cron_pattern)
        return None
    except (ValueError, KeyError) as exc:
        return str(exc)


def matches_cron(cron_pattern, dt):
    """Check if a datetime matches a 5-field cron pattern (min hour day month dow)."""
    return croniter.match(cron_pattern, dt)


def should_run(cron_pattern, last_run_ts):
    """Determine if a job should run, implementing catch-up execution.

    On first run (last_run_ts == 0), only checks the current minute.
    Otherwise, finds the next scheduled time after last_run and checks
    whether it falls within the catch-up window (60 minutes from now).
    This ensures a job scheduled for e.g. 3:00 AM that couldn't run because
    another job was executing will still trigger when checked at 3:05 AM.
    """
    now = time.time()

    if last_run_ts == 0:
        return matches_cron(cron_pattern, datetime.fromtimestamp(now))

    # Work in datetime space to avoid timezone issues with float timestamps
    now_dt = datetime.fromtimestamp(now)
    last_run_dt = datetime.fromtimestamp(last_run_ts)

    # Find the next scheduled time after last run
    cron = croniter(cron_pattern, last_run_dt)
    next_dt = cron.get_next(datetime)

    # Must be in the past or current minute to fire
    if next_dt > now_dt:
        return False

    # Cap lookback to 60 minutes — avoids firing stale schedules after
    # a long container outage.
    MAX_CATCHUP = 60 * 60
    if (now_dt - next_dt).total_seconds() > MAX_CATCHUP:
        return False

    return True
