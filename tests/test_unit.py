"""Unit tests for cron, config validation, and redaction."""

from datetime import datetime

import pytest

from clouddump import cfg, redact
from clouddump.config import validate_jobs
from clouddump.cron import matches_cron, should_run, validate_cron


# ── validate_cron ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern",
    [
        "* * * * *",
        "*/5 * * * *",
        "0 3 * * *",
        "59 23 31 12 6",
    ],
    ids=["all-wildcard", "step-5", "3am-daily", "max-values"],
)
def test_validate_cron_valid(pattern):
    assert validate_cron(pattern) is None


@pytest.mark.parametrize(
    "pattern, reason",
    [
        ("* * *", "too few fields"),
        ("* * * * * *", "too many fields"),
        ("60 * * * *", "minute out of range"),
        ("* 24 * * *", "hour out of range"),
        ("* * 0 * *", "day 0 out of range"),
        ("* * * 13 *", "month out of range"),
        ("* * * * 7", "dow out of range"),
        ("*/0 * * * *", "step zero"),
        ("abc * * * *", "non-numeric"),
    ],
    ids=[
        "too-few", "too-many",
        "minute-60", "hour-24", "day-0", "month-13", "dow-7",
        "step-zero", "non-numeric",
    ],
)
def test_validate_cron_invalid(pattern, reason):
    err = validate_cron(pattern)
    assert err is not None, f"expected error for {reason}"


# ── matches_cron ─────────────────────────────────────────────────────────────


def test_matches_cron_exact():
    dt = datetime(2025, 6, 15, 14, 30)
    assert matches_cron("30 14 * * *", dt) is True


def test_matches_cron_wildcard():
    dt = datetime(2025, 1, 1, 0, 0)
    assert matches_cron("* * * * *", dt) is True


def test_matches_cron_step_hit():
    dt = datetime(2025, 6, 15, 12, 15)
    assert matches_cron("*/15 * * * *", dt) is True


def test_matches_cron_step_miss():
    dt = datetime(2025, 6, 15, 12, 10)
    assert matches_cron("*/15 * * * *", dt) is False


def test_matches_cron_sunday_is_0():
    # 2025-06-15 is a Sunday → cron dow 0
    dt = datetime(2025, 6, 15, 0, 0)
    assert matches_cron("* * * * 0", dt) is True


def test_matches_cron_mismatch():
    dt = datetime(2025, 6, 15, 15, 0)
    assert matches_cron("30 14 * * *", dt) is False


# ── should_run ───────────────────────────────────────────────────────────────


def test_should_run_first_run_matches(monkeypatch):
    # 2025-06-15 03:00:00 UTC
    fake_now = datetime(2025, 6, 15, 3, 0).timestamp()
    monkeypatch.setattr("time.time", lambda: fake_now)
    assert should_run("0 3 * * *", 0) is True


def test_should_run_first_run_no_match(monkeypatch):
    fake_now = datetime(2025, 6, 15, 4, 0).timestamp()
    monkeypatch.setattr("time.time", lambda: fake_now)
    assert should_run("0 3 * * *", 0) is False


def test_should_run_catchup(monkeypatch):
    # Scheduled 03:00, checked at 03:05, last ran at 02:55
    fake_now = datetime(2025, 6, 15, 3, 5).timestamp()
    last_run = datetime(2025, 6, 15, 2, 55).timestamp()
    monkeypatch.setattr("time.time", lambda: fake_now)
    assert should_run("0 3 * * *", last_run) is True


def test_should_run_too_soon(monkeypatch):
    # Last ran 30 seconds ago → not time yet
    fake_now = datetime(2025, 6, 15, 3, 0, 30).timestamp()
    last_run = datetime(2025, 6, 15, 3, 0, 0).timestamp()
    monkeypatch.setattr("time.time", lambda: fake_now)
    assert should_run("0 3 * * *", last_run) is False


def test_should_run_stale_after_long_outage(monkeypatch):
    # Scheduled 03:00, container down for 2 hours, checked at 05:00
    # Should NOT fire — outside 60-minute catch-up window
    fake_now = datetime(2025, 6, 15, 5, 0).timestamp()
    last_run = datetime(2025, 6, 15, 2, 55).timestamp()
    monkeypatch.setattr("time.time", lambda: fake_now)
    assert should_run("0 3 * * *", last_run) is False


# ── validate_jobs ────────────────────────────────────────────────────────────


def _job(**overrides):
    """Build a minimal valid job dict, then apply overrides."""
    base = {"id": "backup1", "type": "s3bucket", "crontab": "0 3 * * *"}
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _fake_which(monkeypatch):
    """Make shutil.which always find the tool so we test config logic, not PATH."""
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/fake")


def test_validate_jobs_valid():
    errors, summary = validate_jobs([_job()])
    assert errors == 0
    assert "backup1" in summary
    assert "s3bucket" in summary
    assert "0 3 * * *" in summary


def test_validate_jobs_missing_id():
    errors, _ = validate_jobs([_job(id="")])
    assert errors >= 1


def test_validate_jobs_missing_type():
    errors, _ = validate_jobs([_job(type="")])
    assert errors >= 1


def test_validate_jobs_unknown_type():
    errors, _ = validate_jobs([_job(type="ftp")])
    assert errors >= 1


def test_validate_jobs_missing_crontab():
    errors, _ = validate_jobs([_job(crontab="")])
    assert errors >= 1


def test_validate_jobs_invalid_crontab():
    errors, _ = validate_jobs([_job(crontab="nope")])
    assert errors >= 1


def test_validate_jobs_duplicate_id():
    errors, _ = validate_jobs([_job(), _job()])
    assert errors >= 1


def test_validate_jobs_timeout_zero():
    errors, _ = validate_jobs([_job(timeout=0)])
    assert errors >= 1


def test_validate_jobs_timeout_negative():
    errors, _ = validate_jobs([_job(timeout=-1)])
    assert errors >= 1


def test_validate_jobs_retries_zero():
    errors, _ = validate_jobs([_job(retries=0)])
    assert errors >= 1


# ── redact ───────────────────────────────────────────────────────────────────


def test_redact_password():
    assert "[REDACTED]" in redact("password: secret123")
    assert "secret123" not in redact("password: secret123")


def test_redact_pass():
    assert "[REDACTED]" in redact("pass: abc")
    assert "abc" not in redact("pass: abc")


def test_redact_token():
    assert "[REDACTED]" in redact("token=xyz")
    assert "xyz" not in redact("token=xyz")


def test_redact_aws_key():
    result = redact("key is AKIAIOSFODNN7EXAMPLE here")
    assert "[REDACTED_AWS_KEY]" in result
    assert "AKIAIOSFODNN7EXAMPLE" not in result


def test_redact_azure_account_key():
    result = redact("AccountKey=abc123;EndpointSuffix=core.windows.net")
    assert "[REDACTED]" in result
    assert "abc123" not in result


def test_redact_sas_url():
    url = "https://store.blob.core.windows.net/c?sv=2021-08&sig=abc&se=2025-01-01"
    result = redact(url)
    assert "?[REDACTED]" in result
    assert "sig=abc" not in result


def test_redact_no_secrets():
    text = "Nothing sensitive here, just a normal log line."
    assert redact(text) == text
