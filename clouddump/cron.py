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
    """Determine if a job should run now.

    Returns True if the current minute matches the cron pattern and at least
    one minute has elapsed since the last run (prevents double-firing within
    the same minute).
    """
    now = time.time()
    now_dt = datetime.fromtimestamp(now)

    if not matches_cron(cron_pattern, now_dt):
        return False

    # Prevent double-firing: at least 60 seconds must have elapsed
    if last_run_ts > 0 and (now - last_run_ts) < 60:
        return False

    return True
