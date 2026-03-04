"""Cron schedule evaluation."""

import time
from datetime import datetime


def validate_cron(cron_pattern):
    """Validate a 5-field cron pattern. Returns an error string, or None if valid.

    Each field must be "*", "*/N" (where N is a positive integer), or an integer
    within the valid range for that field.
    """
    field_bounds = [
        (0, 59, "minute"),
        (0, 23, "hour"),
        (1, 31, "day"),
        (1, 12, "month"),
        (0, 6, "day-of-week"),
    ]
    fields = cron_pattern.split()
    if len(fields) != 5:
        return f"expected 5 fields, got {len(fields)}"
    for i, field in enumerate(fields):
        lo, hi, name = field_bounds[i]
        if field == "*":
            continue
        if field.startswith("*/"):
            rest = field[2:]
            try:
                n = int(rest)
                if n <= 0:
                    return f"{name} '{field}': step must be positive"
            except ValueError:
                return f"{name} '{field}': invalid step value"
            continue
        try:
            v = int(field)
        except ValueError:
            return f"{name} '{field}': expected '*', '*/N', or integer"
        if v < lo or v > hi:
            return f"{name} '{field}': must be {lo}-{hi}"
    return None


def _matches_field(pattern, value):
    """Check if a single cron field matches a time value.

    Supports: "*" (any), "*/N" (step/interval), or an exact integer.
    Ranges (1-5) and lists (1,3,5) are not supported.
    """
    if pattern == "*":
        return True
    if pattern.startswith("*/"):
        step = int(pattern[2:])
        return value % step == 0
    return int(pattern) == value


def matches_cron(cron_pattern, dt):
    """Check if a datetime matches a 5-field cron pattern (min hour day month dow).

    Day of week uses cron convention: 0=Sunday, 1=Monday, ..., 6=Saturday.
    Python's weekday() returns 0=Monday, so we convert via (weekday+1)%7.
    """
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
    """Determine if a job should run, implementing catch-up execution.

    On first run (last_run_ts == 0), only checks the current minute.
    Otherwise, iterates forward minute-by-minute from last_run to now.
    Returns True on the first matching minute — this ensures a job scheduled
    for e.g. 3:00 AM that couldn't run because another job was executing
    will still trigger when checked at 3:05 AM.
    """
    now = time.time()

    if last_run_ts == 0:
        return matches_cron(cron_pattern, datetime.fromtimestamp(now))

    # Align to minute boundaries
    last_minute = int(last_run_ts) // 60 * 60
    current_minute = int(now) // 60 * 60

    # Cap lookback to 60 minutes — avoids firing stale schedules after
    # a long container outage and prevents thousands of iterations.
    MAX_CATCHUP = 60 * 60
    earliest = max(last_minute + 60, current_minute - MAX_CATCHUP)

    ts = earliest
    while ts <= current_minute:
        if matches_cron(cron_pattern, datetime.fromtimestamp(ts)):
            return True
        ts += 60

    return False
