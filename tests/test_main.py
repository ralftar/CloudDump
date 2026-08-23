"""Tests for the per-job orchestration in clouddump.__main__.

_run_one_job owns retries, the three-tier status, the log-file lifecycle and the
report email. That email is the only signal some deployments have that a backup
ran, so the behaviour here is worth pinning down.
"""

import os
from unittest.mock import patch

import pytest

import clouddump
from clouddump.__main__ import _run_one_job


@pytest.fixture(autouse=True)
def _reset_state():
    clouddump.current_job = ""
    clouddump.job_deadline = None
    clouddump.shutdown_requested = False
    yield
    clouddump.current_job = ""
    clouddump.job_deadline = None
    clouddump.shutdown_requested = False


@pytest.fixture
def reports():
    """Capture send_job_report calls as kwargs dicts."""
    captured = []

    def fake(config, version, host, job, exit_code, t_start, t_end, logfile_paths, **kw):
        captured.append({"exit_code": exit_code, "job": job,
                         "logfile_paths": list(logfile_paths), **kw})

    with patch("clouddump.__main__.send_job_report", fake):
        yield captured


def _job(**over):
    base = {"id": "test-job", "type": "pgsql", "retries": 3, "timeout": 60}
    base.update(over)
    return base


def _run(job, exec_results, reports, sleepless=True):
    """Drive _run_one_job with execute_job returning each of *exec_results*."""
    calls = []
    seq = iter(exec_results)

    def fake_execute(j, logfile_path):
        calls.append(logfile_path)
        r = next(seq)
        if isinstance(r, Exception):
            raise r
        return r

    with patch("clouddump.__main__.execute_job", fake_execute):
        if sleepless:
            with patch("clouddump.__main__.time.sleep", lambda _: None):
                rc = _run_one_job(job, {}, "9.9.9", "testhost")
        else:
            rc = _run_one_job(job, {}, "9.9.9", "testhost")
    return rc, calls


# ── exit code and retries ───────────────────────────────────────────────────


def test_success_first_attempt(reports):
    rc, calls = _run(_job(), [0], reports)
    assert rc == 0
    assert len(calls) == 1
    assert reports[0]["status"] == "Success"
    assert reports[0]["attempts_used"] == 1


def test_retries_until_success(reports):
    rc, calls = _run(_job(), [1, 1, 0], reports)
    assert rc == 0
    assert len(calls) == 3


def test_success_after_retry_is_reported_as_warning(reports):
    """A backup that only succeeded on the third try is not a clean success."""
    _run(_job(), [1, 1, 0], reports)
    assert reports[0]["status"] == "Warning"
    assert reports[0]["attempts_used"] == 3


def test_exhausted_retries_is_failure(reports):
    rc, _ = _run(_job(retries=2), [1, 1], reports)
    assert rc == 1
    assert reports[0]["status"] == "Failure"
    assert reports[0]["attempts_used"] == 2


def test_retries_setting_is_honoured(reports):
    _, calls = _run(_job(retries=5), [1, 1, 1, 1, 0], reports)
    assert len(calls) == 5


def test_stops_at_first_success(reports):
    _, calls = _run(_job(retries=5), [0, 0, 0, 0, 0], reports)
    assert len(calls) == 1


# ── crashes ─────────────────────────────────────────────────────────────────


def test_runner_exception_becomes_a_failed_attempt(reports):
    rc, calls = _run(_job(retries=2), [RuntimeError("boom"), 0], reports)
    assert rc == 0
    assert len(calls) == 2, "a crash must be retried like any other failure"


def test_crash_on_every_attempt_reports_failure(reports):
    rc, _ = _run(_job(retries=2), [RuntimeError("boom"), RuntimeError("boom")], reports)
    assert rc == 1
    assert reports[0]["status"] == "Failure"


# ── reporting always happens ────────────────────────────────────────────────


def test_report_sent_exactly_once_per_job(reports):
    _run(_job(retries=3), [1, 1, 0], reports)
    assert len(reports) == 1, "one email per job, not one per attempt"


def test_report_sent_even_when_every_attempt_crashes(reports):
    _run(_job(retries=1), [RuntimeError("boom")], reports)
    assert len(reports) == 1, "a crashing job must still be reported"


def test_one_logfile_per_attempt(reports):
    _run(_job(retries=3), [1, 1, 0], reports)
    assert len(reports[0]["logfile_paths"]) == 3


# ── side effects ────────────────────────────────────────────────────────────


def test_logfiles_are_cleaned_up(reports):
    _run(_job(retries=2), [1, 0], reports)
    for path in reports[0]["logfile_paths"]:
        assert not os.path.exists(path)


def test_current_job_is_set_during_and_cleared_after(reports):
    seen = []

    def fake_execute(j, logfile_path):
        seen.append(clouddump.current_job)
        return 0

    with patch("clouddump.__main__.execute_job", fake_execute):
        _run_one_job(_job(), {}, "9.9.9", "testhost")

    assert seen == ["test-job"]
    assert clouddump.current_job == ""


def test_current_job_cleared_even_when_reporting_raises():
    def boom(*a, **k):
        raise RuntimeError("smtp exploded")

    with patch("clouddump.__main__.execute_job", lambda j, p: 0), \
         patch("clouddump.__main__.send_job_report", boom):
        with pytest.raises(RuntimeError):
            _run_one_job(_job(), {}, "9.9.9", "testhost")

    assert clouddump.current_job == "", "a failed report must not leak job context"


def test_deadline_is_set_per_attempt_and_cleared(reports):
    seen = []

    def fake_execute(j, logfile_path):
        seen.append(clouddump.job_deadline)
        return 0

    with patch("clouddump.__main__.execute_job", fake_execute):
        _run_one_job(_job(timeout=1234), {}, "9.9.9", "testhost")

    assert seen[0] is not None
    assert clouddump.job_deadline is None, "deadline must not leak past the job"


def test_zero_retries_reports_failure_not_stale_state(reports):
    """Validation forbids retries < 1, but the guard must not read a stale result."""
    rc, calls = _run(_job(retries=0), [], reports)
    assert calls == []
    assert rc == 1
    assert reports[0]["status"] == "Failure"
