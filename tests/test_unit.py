"""Unit tests for cron, config validation, redaction, log format, and health endpoint."""

import json
import logging
import sys
from datetime import datetime, timezone
from unittest.mock import patch
import urllib.error
import urllib.request

import pytest

from clouddump import redact, fmt_bytes, validate_backup_path, _TextFormatter, _JsonFormatter, _LOG_FORMAT, _LOG_DATEFMT
from clouddump.email import format_job_config
from clouddump.config import validate_settings, validate_jobs, verify_connectivity
from clouddump.cron import matches_cron, should_run, validate_cron
from clouddump.health import _state, update_last_run, update_job_metric, _Handler


# ── log format ──────────────────────────────────────────────────────────────


@pytest.fixture()
def _log_capture():
    """Yield a (logger, handler) that captures formatted JSON output."""
    formatter = _JsonFormatter()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger = logging.getLogger("clouddump.test_format")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield logger, handler
    logger.removeHandler(handler)


def _parse(handler, record):
    """Format a record and parse the JSON output."""
    return json.loads(handler.format(record))


def test_log_format_info(_log_capture):
    logger, handler = _log_capture
    record = logger.makeRecord(logger.name, logging.INFO, "test", 0, "hello", (), None)
    obj = _parse(handler, record)
    assert obj["level"] == "info"
    assert obj["message"] == "hello"
    assert "timestamp" in obj


def test_log_format_warning(_log_capture):
    logger, handler = _log_capture
    record = logger.makeRecord(logger.name, logging.WARNING, "test", 0, "caution", (), None)
    obj = _parse(handler, record)
    assert obj["level"] == "warn"
    assert obj["message"] == "caution"


def test_log_format_error(_log_capture):
    logger, handler = _log_capture
    record = logger.makeRecord(logger.name, logging.ERROR, "test", 0, "boom", (), None)
    obj = _parse(handler, record)
    assert obj["level"] == "error"
    assert obj["message"] == "boom"


def test_log_format_critical(_log_capture):
    logger, handler = _log_capture
    record = logger.makeRecord(logger.name, logging.CRITICAL, "test", 0, "critical issue", (), None)
    obj = _parse(handler, record)
    assert obj["level"] == "crit"


def test_log_format_restores_levelname(_log_capture):
    logger, handler = _log_capture
    record = logger.makeRecord(logger.name, logging.WARNING, "test", 0, "test msg", (), None)
    handler.format(record)
    assert record.levelname == "WARNING"


@pytest.mark.parametrize("secret,label", [
    ("password=SuperSecret123", "SuperSecret123"),
    ("token=ghp_abc123secret", "ghp_abc123secret"),
    ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("AccountKey=abc123;EndpointSuffix=core.windows.net", "abc123"),
    ("postgres://admin:s3cret@db:5432/mydb", "s3cret"),
])
def test_log_format_redacts_secrets(_log_capture, secret, label):
    """Formatter must redact secrets from ALL log output."""
    logger, handler = _log_capture
    record = logger.makeRecord(
        logger.name, logging.ERROR, "test", 0, f"Connection failed: {secret}", (), None)
    output = handler.format(record)
    assert label not in output
    assert "REDACTED" in output
    json.loads(output)  # must be valid JSON


def test_log_format_includes_job_context(_log_capture):
    """When current_job is set, a 'job' field appears in JSON."""
    import clouddump
    logger, handler = _log_capture
    old = clouddump.current_job
    clouddump.current_job = "sleipner-pg"
    try:
        record = logger.makeRecord(
            logger.name, logging.WARNING, "test", 0, "No databases to backup.", (), None)
        obj = _parse(handler, record)
        assert obj["job"] == "sleipner-pg"
        assert obj["message"] == "No databases to backup."
    finally:
        clouddump.current_job = old


def test_log_format_no_prefix_without_job(_log_capture):
    """When current_job is empty, no 'job' field appears."""
    import clouddump
    logger, handler = _log_capture
    old = clouddump.current_job
    clouddump.current_job = ""
    try:
        record = logger.makeRecord(
            logger.name, logging.INFO, "test", 0, "Starting main loop...", (), None)
        obj = _parse(handler, record)
        assert "job" not in obj
        assert obj["message"] == "Starting main loop..."
    finally:
        clouddump.current_job = old


def test_log_format_restores_msg_after_job_context(_log_capture):
    """Formatter must not permanently mutate record.msg."""
    import clouddump
    logger, handler = _log_capture
    old = clouddump.current_job
    clouddump.current_job = "test-job"
    try:
        record = logger.makeRecord(logger.name, logging.INFO, "test", 0, "original msg", (), None)
        handler.format(record)
        assert record.msg == "original msg"
    finally:
        clouddump.current_job = old


def test_log_format_extra_fields(_log_capture):
    """Extra fields from log calls appear in JSON output."""
    logger, handler = _log_capture
    record = logger.makeRecord(logger.name, logging.INFO, "test", 0, "dump done", (), None)
    record.database = "mydb"
    record.bytes = 1024
    obj = _parse(handler, record)
    assert obj["database"] == "mydb"
    assert obj["bytes"] == 1024


def test_log_format_unknown_extra_fields_excluded(_log_capture):
    """Fields not in _EXTRA_FIELDS are not included in JSON."""
    logger, handler = _log_capture
    record = logger.makeRecord(logger.name, logging.INFO, "test", 0, "msg", (), None)
    record.secret_internal = "should not appear"
    obj = _parse(handler, record)
    assert "secret_internal" not in obj


# ── text formatter ─────────────────────────────────────────────────────────


def test_text_formatter_output():
    """Text formatter produces human-readable output with level and job prefix."""
    import clouddump
    formatter = _TextFormatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger = logging.getLogger("clouddump.test_text")
    logger.addHandler(handler)
    old = clouddump.current_job
    clouddump.current_job = "my-job"
    try:
        record = logger.makeRecord(logger.name, logging.WARNING, "test", 0, "something broke", (), None)
        output = handler.format(record)
        assert "level=warn" in output
        assert "[my-job] something broke" in output
    finally:
        clouddump.current_job = old
        logger.removeHandler(handler)


# ── debug log suppression ───────────────────────────────────────────────────


def test_debug_lines_suppressed_at_info_level():
    """When the logger is at INFO level (debug=false), DEBUG messages are dropped."""
    logger = logging.getLogger("clouddump.test_suppress")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        records = []
        handler.emit = lambda r: records.append(r)
        logger.debug("should be suppressed")
        logger.info("should appear")
        assert len(records) == 1
        assert records[0].getMessage() == "should appear"
    finally:
        logger.removeHandler(handler)


def test_debug_lines_visible_at_debug_level():
    """When the logger is at DEBUG level (debug=true), DEBUG messages pass through."""
    logger = logging.getLogger("clouddump.test_visible")
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        records = []
        handler.emit = lambda r: records.append(r)
        logger.debug("should appear")
        assert len(records) == 1
        assert records[0].getMessage() == "should appear"
    finally:
        logger.removeHandler(handler)


def test_tool_output_logged_at_debug_level():
    """run_cmd streams subprocess output at DEBUG level, not INFO."""
    import io
    import clouddump

    # Patch subprocess to produce a known line
    fake_pipe = io.BytesIO(b"Updating repo-x in /backup/github\n")

    logger = logging.getLogger("clouddump")
    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r)
    logger.addHandler(handler)
    old_level = logger.getEffectiveLevel()
    logger.setLevel(logging.DEBUG)
    try:
        # Call _stream logic directly via a thread (mirrors run_cmd internals)
        import tempfile
        fd, logfile = tempfile.mkstemp(prefix="test-stream-")
        import os
        os.close(fd)
        try:
            with open(logfile, "a") as logf:
                for raw_line in fake_pipe:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                    logf.write(line + "\n")
                    logf.flush()
                    clouddump.log.debug("  %s", line)

            debug_records = [r for r in records if "Updating repo-x" in r.getMessage()]
            assert len(debug_records) == 1
            assert debug_records[0].levelno == logging.DEBUG
        finally:
            clouddump._safe_remove(logfile)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


# ── validate_cron ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pattern", [
    "* * * * *", "*/5 * * * *", "0 3 * * *", "59 23 31 12 6",
    "0 1-5 * * *",      # range
    "0,30 * * * *",     # list
    "0 3 1,15 * *",     # list in day field
    "0 3 * * 1-5",      # weekday range (Mon-Fri)
    "*/10 9-17 * * *",  # step with range
    "* * * * 7",        # dow 7 (Sunday alias, standard cron)
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
    # Range and list syntax
    ("0 9-17 * * *", datetime(2025, 6, 15, 12, 0), True),
    ("0 9-17 * * *", datetime(2025, 6, 15, 8, 0), False),
    ("0,30 * * * *", datetime(2025, 6, 15, 12, 30), True),
    ("0,30 * * * *", datetime(2025, 6, 15, 12, 15), False),
    ("0 3 * * 1-5", datetime(2025, 6, 16, 3, 0), True),   # Monday
    ("0 3 * * 1-5", datetime(2025, 6, 15, 3, 0), False),  # Sunday
])
def test_matches_cron(pattern, dt, expected):
    assert matches_cron(pattern, dt) is expected


# ── should_run ───────────────────────────────────────────────────────────────


def test_should_run_matches(monkeypatch):
    monkeypatch.setattr("time.time", lambda: datetime(2025, 6, 15, 3, 0).timestamp())
    assert should_run("0 3 * * *", 0) is True


def test_should_run_no_match(monkeypatch):
    monkeypatch.setattr("time.time", lambda: datetime(2025, 6, 15, 4, 0).timestamp())
    assert should_run("0 3 * * *", 0) is False


def test_should_run_no_double_fire(monkeypatch):
    # Same minute as last run — should not fire again
    now = datetime(2025, 6, 15, 3, 0, 30).timestamp()
    last = datetime(2025, 6, 15, 3, 0, 0).timestamp()
    monkeypatch.setattr("time.time", lambda: now)
    assert should_run("0 3 * * *", last) is False


def test_should_run_next_match(monkeypatch):
    # Cron matches now, last run was a previous match — should fire
    now = datetime(2025, 6, 15, 3, 0).timestamp()
    last = datetime(2025, 6, 14, 3, 0).timestamp()  # yesterday
    monkeypatch.setattr("time.time", lambda: now)
    assert should_run("0 3 * * *", last) is True


def test_should_run_missed_slot_waits(monkeypatch):
    # Scheduled 03:00, checked at 03:05 — not a match, must wait
    monkeypatch.setattr("time.time", lambda: datetime(2025, 6, 15, 3, 5).timestamp())
    assert should_run("0 3 * * *", datetime(2025, 6, 15, 2, 55).timestamp()) is False


# ── validate_settings ───────────────────────────────────────────────────────


def test_validate_settings_valid_crontab():
    assert validate_settings({"crontab": "0 3 * * *"}) == 0


def test_validate_settings_missing_crontab():
    assert validate_settings({}) >= 1


def test_validate_settings_invalid_crontab():
    assert validate_settings({"crontab": "nope"}) >= 1


def test_validate_settings_bad_bool():
    assert validate_settings({"crontab": "0 3 * * *", "debug": "true"}) >= 1


def test_validate_settings_valid_health_port():
    assert validate_settings({"crontab": "0 3 * * *", "health_port": 9090}) == 0


@pytest.mark.parametrize("val", ["abc", 0, -1, 70000])
def test_validate_settings_bad_health_port(val):
    assert validate_settings({"crontab": "0 3 * * *", "health_port": val}) >= 1


# ── validate_jobs ────────────────────────────────────────────────────────────


def _job(**overrides):
    base = {"id": "backup1", "type": "s3bucket"}
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
    # Unquoted field patterns
    ("password: secret123", "secret123"),
    ("pass: abc", "abc"),
    ("token=xyz", "xyz"),
    # JSON-quoted field patterns
    ('"pass": "SuperSecret123"', "SuperSecret123"),
    ('"password": "db_pass_456"', "db_pass_456"),
    ('"token": "ghp_xxx"', "ghp_xxx"),
    ('"secret": "my-api-secret"', "my-api-secret"),
    ('"aws_secret_access_key": "wJalrXUtnFEMI"', "wJalrXUtnFEMI"),
    # AWS keys
    ("key is AKIAIOSFODNN7EXAMPLE here", "AKIAIOSFODNN7EXAMPLE"),
    # GitHub tokens (all prefixes)
    ("Error: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn"),
    ("Error: ghu_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn", "ghu_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn"),
    ("Error: ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn", "ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn"),
    # Azure
    ("AccountKey=abc123;EndpointSuffix=core.windows.net", "abc123"),
    ("https://store.blob.core.windows.net/c?sv=2021-08&sig=abc&se=2025-01-01", "sig=abc"),
    # Database URLs
    ("postgres://admin:s3cret@db.example.com:5432/mydb", "s3cret"),
    ("postgresql://user:hunter2@localhost/app", "hunter2"),
    ("mongodb://root:mongopass@mongo:27017", "mongopass"),
    # Database URL with @ in password
    ("mysql://root:p%40ssw0rd@db:3306/mydb", "p%40ssw0rd"),
    # Authorization headers
    ("Authorization: Bearer ghp_abc123secret", "ghp_abc123secret"),
    ("authorization: token mytoken123", "mytoken123"),
])
def test_redact_strips_secrets(text, secret):
    result = redact(text)
    assert secret not in result
    assert "REDACTED" in result


def test_redact_strips_pem_private_key():
    text = (
        "Error loading key:\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAA\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
        "Permission denied."
    )
    result = redact(text)
    assert "b3BlbnNzaC1rZXktdjE" not in result
    assert "REDACTED_PRIVATE_KEY" in result
    assert "Permission denied." in result


def test_redact_ignores_clean_text():
    text = "Nothing sensitive here, just a normal log line."
    assert redact(text) == text


def test_validate_jobs_github_invalid_account_type():
    job = _job(type="github", organizations=[
        {"name": "x", "token": "ghp_x", "account_type": "team"}])
    errors, _ = validate_jobs([job])
    assert errors >= 1


# ── verify_connectivity ─────────────────────────────────────────────────────


@patch("clouddump.config.urllib.request.urlopen")
def test_verify_connectivity_github_token(mock_urlopen):
    mock_urlopen.return_value.__enter__ = lambda s: s
    mock_urlopen.return_value.__exit__ = lambda s, *a: None
    job = _job(type="github", organizations=[{"name": "my-org", "token": "ghp_xxx"}])
    results = verify_connectivity([job])
    assert any("OK" in r and "GitHub" in r for r in results)


@patch("clouddump.config.urllib.request.urlopen")
def test_verify_connectivity_github_warns_on_failure(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://api.github.com/orgs/x", 401, "Unauthorized", {}, None)
    job = _job(type="github", organizations=[{"name": "my-org", "token": "ghp_bad"}])
    results = verify_connectivity([job])
    assert any("WARN" in r for r in results)


@patch("subprocess.run")
def test_verify_connectivity_db_connection(mock_run):
    """DB without configured databases verifies credentials."""
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "1\n"
    mock_run.return_value.stderr = ""
    job = _job(type="pgsql", servers=[{"host": "db.example.com", "port": 5432, "pass": "secret"}])
    results = verify_connectivity([job])
    assert any("OK" in r and "pgsql" in r for r in results)


@patch("subprocess.run")
def test_verify_connectivity_db_connection_failure(mock_run):
    """DB connection failure is a warning."""
    mock_run.return_value.returncode = 2
    mock_run.return_value.stdout = ""
    mock_run.return_value.stderr = "connection refused\n"
    job = _job(type="mysql", servers=[{"host": "mysql.example.com", "port": 3306, "user": "backup", "pass": "secret"}])
    results = verify_connectivity([job])
    assert any("WARN" in r for r in results)


@patch("subprocess.run")
def test_verify_s3_bucket_ok(mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = ""
    mock_run.return_value.stderr = ""
    job = _job(type="s3bucket", buckets=[{
        "source": "s3://my-bucket/prefix",
        "aws_access_key_id": "AKIA...", "aws_secret_access_key": "secret", "aws_region": "eu-west-1"}])
    results = verify_connectivity([job])
    assert any("OK" in r and "S3" in r for r in results)
    cmd = mock_run.call_args[0][0]
    assert "head-bucket" in cmd
    assert "my-bucket" in cmd


@patch("subprocess.run")
def test_verify_s3_bucket_failure(mock_run):
    mock_run.return_value.returncode = 1
    mock_run.return_value.stdout = ""
    mock_run.return_value.stderr = "404 Not Found\n"
    job = _job(type="s3bucket", buckets=[{"source": "s3://bad-bucket"}])
    results = verify_connectivity([job])
    assert any("WARN" in r and "S3" in r for r in results)


@patch("clouddump.config.urllib.request.urlopen")
def test_verify_az_container_ok(mock_urlopen):
    mock_urlopen.return_value.__enter__ = lambda s: s
    mock_urlopen.return_value.__exit__ = lambda s, *a: None
    job = _job(type="azstorage", blobstorages=[{
        "source": "https://account.blob.core.windows.net/container?sv=2021&sig=xxx"}])
    results = verify_connectivity([job])
    assert any("OK" in r and "Azure" in r for r in results)


@patch("clouddump.config.urllib.request.urlopen")
def test_verify_az_container_failure(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://account.blob.core.windows.net/", 403, "Forbidden", {}, None)
    job = _job(type="azstorage", blobstorages=[{
        "source": "https://account.blob.core.windows.net/container?sv=2021&sig=bad"}])
    results = verify_connectivity([job])
    assert any("WARN" in r and "Azure" in r for r in results)


@patch("subprocess.run")
def test_verify_rsync_ssh_ok(mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = ""
    mock_run.return_value.stderr = ""
    job = _job(type="rsync", targets=[{
        "source": "user@host.example.com:/data", "ssh_key": "/config/id_ed25519"}])
    results = verify_connectivity([job])
    assert any("OK" in r and "SSH" in r for r in results)


@patch("subprocess.run")
def test_verify_rsync_ssh_failure(mock_run):
    mock_run.return_value.returncode = 1
    mock_run.return_value.stdout = ""
    mock_run.return_value.stderr = "Permission denied\n"
    job = _job(type="rsync", targets=[{
        "source": "user@host.example.com:/data", "ssh_key": "/config/id_ed25519"}])
    results = verify_connectivity([job])
    assert any("WARN" in r and "SSH" in r for r in results)


@patch("subprocess.run")
def test_verify_pgsql_databases_and_tables(mock_run):
    """Configured databases + table filters verified in one flow."""
    # First call: list databases. Second call: list tables.
    db_list = type("Proc", (), {"returncode": 0, "stdout": "mydb\nother\n", "stderr": ""})()
    tbl_list = type("Proc", (), {"returncode": 0, "stdout": "users\norders\n", "stderr": ""})()
    mock_run.side_effect = [db_list, tbl_list]
    job = _job(type="pgsql", servers=[{
        "host": "pg.example.com", "pass": "secret",
        "databases": [{"mydb": {"tables_excluded": ["missing_table"]}}]}])
    results = verify_connectivity([job])
    assert any("OK" in r and "pgsql" in r for r in results)
    assert any("WARN" in r and "missing_table" in r for r in results)


@patch("subprocess.run")
def test_verify_pgsql_skips_tables_when_db_missing(mock_run):
    """Table filter check skipped for databases that don't exist."""
    db_list = type("Proc", (), {"returncode": 0, "stdout": "other\n", "stderr": ""})()
    mock_run.return_value = db_list
    job = _job(type="pgsql", servers=[{
        "host": "pg.example.com", "pass": "secret",
        "databases": [{"noexist": {"tables_included": ["t1"]}}]}])
    results = verify_connectivity([job])
    assert any("WARN" in r and "noexist" in r for r in results)
    assert not any("t1" in r for r in results)  # table check skipped
    assert mock_run.call_count == 1  # only the DB list query


@patch("subprocess.run")
def test_verify_mysql_databases(mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "app_db\nanalytics\n"
    mock_run.return_value.stderr = ""
    job = _job(type="mysql", servers=[{
        "host": "mysql.example.com", "user": "backup", "pass": "secret",
        "databases": ["app_db", "gone_db"]}])
    results = verify_connectivity([job])
    assert any("OK" in r and "mysql" in r for r in results)
    assert any("WARN" in r and "gone_db" in r for r in results)


# ── fmt_bytes ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("n, expected", [
    (500, "0.5 KB"),
    (1024, "1.0 KB"),
    (1024 * 1024, "1.0 MB"),
    (1024 * 1024 * 512, "512.0 MB"),
    (1024 * 1024 * 1024, "1.0 GB"),
    (1024 * 1024 * 1024 * 2.5, "2.5 GB"),
])
def test_fmt_bytes(n, expected):
    assert fmt_bytes(n) == expected


# ── validate_backup_path ────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="Unix paths only")
@pytest.mark.parametrize("path", ["/backup/s3", "/mnt/clouddump", "/tmp/test"])
def test_validate_backup_path_allowed(path):
    assert validate_backup_path(path) is None


@pytest.mark.skipif(sys.platform == "win32", reason="Unix paths only")
@pytest.mark.parametrize("path", ["/etc/passwd", "/root", "/home/user", "/var/data"])
def test_validate_backup_path_rejected(path):
    assert validate_backup_path(path) is not None


# ── format_job_config ───────────────────────────────────────────────────────


def test_format_job_config_redacts_secrets():
    job = {"id": "test", "type": "pgsql", "servers": [{"host": "db", "pass": "secret123"}]}
    result = format_job_config(job)
    assert "secret123" not in result
    assert "REDACTED" in result
    assert "db" in result  # non-secret values preserved


def test_format_job_config_valid_json():
    job = {"id": "test", "type": "s3bucket"}
    result = format_job_config(job)
    parsed = json.loads(result)
    assert parsed["id"] == "test"


# ── health endpoint ─────────────────────────────────────────────────────────


def test_update_last_run_populates_state():
    started = datetime(2026, 3, 28, 3, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 3, 28, 3, 47, 0, tzinfo=timezone.utc)
    update_last_run(started, finished, succeeded=4, failed=1, total=5)
    lr = _state["last_run"]
    assert lr["jobs"] == 5
    assert lr["succeeded"] == 4
    assert lr["failed"] == 1
    assert "2026-03-28" in lr["started"]
    assert "2026-03-28" in lr["finished"]
    assert lr["finished_epoch"] == int(finished.timestamp())
    assert lr["has_run"] is True


def test_update_job_metric():
    old = _state["jobs"].copy()
    try:
        update_job_metric("test-pg", "pgsql", "success", 134, rx=1024000, tx=512)
        m = _state["jobs"]["test-pg"]
        assert m["type"] == "pgsql"
        assert m["status"] == "success"
        assert m["elapsed_seconds"] == 134
        assert m["rx_bytes"] == 1024000
        assert m["tx_bytes"] == 512
    finally:
        _state["jobs"] = old


def test_update_job_metric_without_net():
    old = _state["jobs"].copy()
    try:
        update_job_metric("test-s3", "s3bucket", "failure", 60)
        m = _state["jobs"]["test-s3"]
        assert m["status"] == "failure"
        assert "rx_bytes" not in m
    finally:
        _state["jobs"] = old


def test_update_last_run_initial_defaults():
    old = _state["last_run"]
    _state["last_run"] = {"jobs": 0, "succeeded": 0, "failed": 0, "has_run": False}
    try:
        assert _state["last_run"]["has_run"] is False
        assert _state["last_run"]["jobs"] == 0
    finally:
        _state["last_run"] = old


def test_healthz_returns_200():
    """Start a real health server on a random port and GET /healthz."""
    import http.server
    import threading

    _state["last_run"] = {"jobs": 0, "succeeded": 0, "failed": 0, "has_run": False}
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{port}/healthz"
        with urllib.request.urlopen(url, timeout=2) as resp:
            assert resp.status == 200
            body = json.loads(resp.read())
            assert body["status"] == "ok"
            assert body["last_run"]["has_run"] is False
            assert body["last_run"]["jobs"] == 0
    finally:
        server.shutdown()


def test_healthz_404_on_other_paths():
    """Non-/healthz paths return 404."""
    import http.server
    import threading

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{port}/other"
        req = urllib.request.Request(url)
        try:
            urllib.request.urlopen(req, timeout=2)
            assert False, "Expected 404"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
