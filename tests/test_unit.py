"""Unit tests for cron, config validation, and redaction."""

from datetime import datetime
from unittest.mock import patch
import urllib.error

import pytest

from clouddump import redact
from clouddump.config import _check_github, validate_jobs, verify_connectivity
from clouddump.cron import matches_cron, should_run, validate_cron


# ── validate_cron ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pattern", [
    "* * * * *", "*/5 * * * *", "0 3 * * *", "59 23 31 12 6",
])
def test_validate_cron_valid(pattern):
    assert validate_cron(pattern) is None


@pytest.mark.parametrize("pattern", [
    "* * *",            # too few fields
    "* * * * * *",      # too many fields
    "60 * * * *",       # minute out of range
    "* 24 * * *",       # hour out of range
    "* * 0 * *",        # day 0 out of range
    "* * * 13 *",       # month out of range
    "* * * * 7",        # dow out of range
    "*/0 * * * *",      # step zero
    "abc * * * *",      # non-numeric
])
def test_validate_cron_invalid(pattern):
    assert validate_cron(pattern) is not None


# ── matches_cron ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pattern, dt, expected", [
    ("30 14 * * *", datetime(2025, 6, 15, 14, 30), True),
    ("30 14 * * *", datetime(2025, 6, 15, 15, 0), False),
    ("*/15 * * * *", datetime(2025, 6, 15, 12, 15), True),
    ("*/15 * * * *", datetime(2025, 6, 15, 12, 10), False),
    ("* * * * 0", datetime(2025, 6, 15, 0, 0), True),  # Sunday
])
def test_matches_cron(pattern, dt, expected):
    assert matches_cron(pattern, dt) is expected


# ── should_run ───────────────────────────────────────────────────────────────


def test_should_run_first_run_matches(monkeypatch):
    monkeypatch.setattr("time.time", lambda: datetime(2025, 6, 15, 3, 0).timestamp())
    assert should_run("0 3 * * *", 0) is True


def test_should_run_first_run_no_match(monkeypatch):
    monkeypatch.setattr("time.time", lambda: datetime(2025, 6, 15, 4, 0).timestamp())
    assert should_run("0 3 * * *", 0) is False


def test_should_run_catchup(monkeypatch):
    # Scheduled 03:00, checked at 03:05, last ran at 02:55
    monkeypatch.setattr("time.time", lambda: datetime(2025, 6, 15, 3, 5).timestamp())
    assert should_run("0 3 * * *", datetime(2025, 6, 15, 2, 55).timestamp()) is True


def test_should_run_too_soon(monkeypatch):
    monkeypatch.setattr("time.time", lambda: datetime(2025, 6, 15, 3, 0, 30).timestamp())
    assert should_run("0 3 * * *", datetime(2025, 6, 15, 3, 0, 0).timestamp()) is False


def test_should_run_stale_after_long_outage(monkeypatch):
    # 2-hour gap — outside 60-minute catch-up window
    monkeypatch.setattr("time.time", lambda: datetime(2025, 6, 15, 5, 0).timestamp())
    assert should_run("0 3 * * *", datetime(2025, 6, 15, 2, 55).timestamp()) is False


# ── validate_jobs ────────────────────────────────────────────────────────────


def _job(**overrides):
    base = {"id": "backup1", "type": "s3bucket", "crontab": "0 3 * * *"}
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _fake_which(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/fake")


def test_validate_jobs_valid():
    errors, summary = validate_jobs([_job()])
    assert errors == 0
    assert "backup1" in summary


@pytest.mark.parametrize("overrides", [
    {"id": ""},
    {"type": ""},
    {"type": "ftp"},
    {"crontab": ""},
    {"crontab": "nope"},
    {"timeout": 0},
    {"timeout": -1},
    {"retries": 0},
])
def test_validate_jobs_rejects(overrides):
    errors, _ = validate_jobs([_job(**overrides)])
    assert errors >= 1


def test_validate_jobs_github_valid():
    errors, summary = validate_jobs([_job(type="github")])
    assert errors == 0
    assert "github" in summary


def test_validate_jobs_mysql_valid():
    errors, summary = validate_jobs([_job(type="mysql")])
    assert errors == 0
    assert "mysql" in summary


def test_validate_jobs_duplicate_id():
    errors, _ = validate_jobs([_job(), _job()])
    assert errors >= 1


# ── redact ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, secret", [
    ("password: secret123", "secret123"),
    ("pass: abc", "abc"),
    ("token=xyz", "xyz"),
    ("key is AKIAIOSFODNN7EXAMPLE here", "AKIAIOSFODNN7EXAMPLE"),
    ("AccountKey=abc123;EndpointSuffix=core.windows.net", "abc123"),
    ("https://store.blob.core.windows.net/c?sv=2021-08&sig=abc&se=2025-01-01", "sig=abc"),
    ("postgres://admin:s3cret@db.example.com:5432/mydb", "s3cret"),
    ("postgresql://user:hunter2@localhost/app", "hunter2"),
    ("mongodb://root:mongopass@mongo:27017", "mongopass"),
    ("Authorization: Bearer ghp_abc123secret", "ghp_abc123secret"),
    ("authorization: token mytoken123", "mytoken123"),
])
def test_redact_strips_secrets(text, secret):
    result = redact(text)
    assert secret not in result
    assert "REDACTED" in result


def test_redact_ignores_clean_text():
    text = "Nothing sensitive here, just a normal log line."
    assert redact(text) == text


# ── _check_github ───────────────────────────────────────────────────────────


@patch("clouddump.config.urllib.request.urlopen")
def test_check_github_org_success(mock_urlopen):
    mock_urlopen.return_value.__enter__ = lambda s: s
    mock_urlopen.return_value.__exit__ = lambda s, *a: None
    assert _check_github("my-org", "ghp_validtoken", "org") is None


@patch("clouddump.config.urllib.request.urlopen")
def test_check_github_user_success(mock_urlopen):
    mock_urlopen.return_value.__enter__ = lambda s: s
    mock_urlopen.return_value.__exit__ = lambda s, *a: None
    assert _check_github("my-user", "ghp_validtoken", "user") is None
    # Verify it used /users/ not /orgs/
    url = mock_urlopen.call_args[0][0].full_url
    assert "/users/my-user" in url


@patch("clouddump.config.urllib.request.urlopen")
def test_check_github_bad_token(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://api.github.com/orgs/x", 401, "Unauthorized", {}, None)
    result = _check_github("my-org", "ghp_badtoken")
    assert "authentication failed" in result


@patch("clouddump.config.urllib.request.urlopen")
def test_check_github_forbidden(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://api.github.com/orgs/x", 403, "Forbidden", {}, None)
    result = _check_github("my-org", "ghp_limited")
    assert "forbidden" in result


@patch("clouddump.config.urllib.request.urlopen")
def test_check_github_not_found(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://api.github.com/orgs/x", 404, "Not Found", {}, None)
    result = _check_github("no-such-org", "ghp_token")
    assert "not found" in result


@patch("clouddump.config.urllib.request.urlopen")
def test_check_github_network_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError("Name or service not known")
    result = _check_github("my-org", "ghp_token")
    assert "cannot reach" in result


def test_validate_jobs_github_invalid_account_type():
    job = _job(type="github", organizations=[
        {"name": "x", "token": "ghp_x", "account_type": "team"}])
    errors, _ = validate_jobs([job])
    assert errors >= 1


# ── verify_connectivity ─────────────────────────────────────────────────────


@patch("clouddump.config._check_github", return_value=None)
def test_verify_connectivity_github_org(mock_gh):
    job = _job(type="github", organizations=[{"name": "my-org", "token": "ghp_xxx"}])
    verify_connectivity([job])
    mock_gh.assert_called_once_with("my-org", "ghp_xxx", "org")


@patch("clouddump.config._check_github", return_value=None)
def test_verify_connectivity_github_user(mock_gh):
    job = _job(type="github", organizations=[
        {"name": "my-user", "token": "ghp_xxx", "account_type": "user"}])
    verify_connectivity([job])
    mock_gh.assert_called_once_with("my-user", "ghp_xxx", "user")


@patch("clouddump.config._check_github", return_value="auth failed")
def test_verify_connectivity_github_warns_on_failure(mock_gh):
    """GitHub check failure is a warning, not a crash."""
    job = _job(type="github", organizations=[{"name": "my-org", "token": "ghp_bad"}])
    verify_connectivity([job])  # should not raise
    mock_gh.assert_called_once()


@patch("clouddump.config._check_github")
def test_verify_connectivity_skips_invalid_account_type(mock_gh):
    """Invalid account_type is caught by validate_jobs, not verify_connectivity."""
    job = _job(type="github", organizations=[
        {"name": "x", "token": "ghp_x", "account_type": "team"}])
    verify_connectivity([job])
    mock_gh.assert_not_called()
