"""Unit tests for job runners — command construction, validation, and error paths.

Every test mocks run_cmd so no external tools are needed.
"""

import os

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _capture_cmd(monkeypatch, module_path, rc=0):
    """Patch run_cmd in *module_path* to capture the command list and return *rc*."""
    calls = []

    def fake_run_cmd(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return rc

    monkeypatch.setattr(module_path, fake_run_cmd)
    return calls


@pytest.fixture(autouse=True)
def _tmp_logfile(tmp_path):
    """Provide a throwaway logfile path for every test."""
    return str(tmp_path / "test.log")


# ── S3 runner ───────────────────────────────────────────────────────────────


class TestS3Runner:
    """Tests for clouddump.job_s3.run_s3_sync."""

    @staticmethod
    def _cfg(**overrides):
        base = {
            "source": "s3://my-bucket",
            "destination": "/backup/s3",
            "aws_access_key_id": "AKIAEXAMPLE",
            "aws_secret_access_key": "secret",
            "aws_region": "eu-west-1",
        }
        base.update(overrides)
        return base

    def test_basic_sync(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_s3 import run_s3_sync

        dest = str(tmp_path / "s3out")
        calls = _capture_cmd(monkeypatch, "clouddump.job_s3.run_cmd")

        rc = run_s3_sync(self._cfg(destination=dest), _tmp_logfile)

        assert rc == 0
        assert len(calls) == 1
        cmd = calls[0][0]
        assert cmd[:3] == ["aws", "s3", "sync"]
        assert "s3://my-bucket" in cmd
        assert dest in cmd
        assert "--delete" in cmd

    def test_delete_disabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_s3 import run_s3_sync

        dest = str(tmp_path / "s3out")
        calls = _capture_cmd(monkeypatch, "clouddump.job_s3.run_cmd")

        run_s3_sync(self._cfg(destination=dest, delete_destination=False), _tmp_logfile)

        assert "--delete" not in calls[0][0]

    def test_endpoint_url(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_s3 import run_s3_sync

        dest = str(tmp_path / "s3out")
        calls = _capture_cmd(monkeypatch, "clouddump.job_s3.run_cmd")

        run_s3_sync(self._cfg(destination=dest, endpoint_url="http://minio:9000"), _tmp_logfile)

        cmd = calls[0][0]
        idx = cmd.index("--endpoint-url")
        assert cmd[idx + 1] == "http://minio:9000"

    def test_env_credentials(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_s3 import run_s3_sync

        dest = str(tmp_path / "s3out")
        calls = _capture_cmd(monkeypatch, "clouddump.job_s3.run_cmd")

        run_s3_sync(self._cfg(destination=dest), _tmp_logfile)

        env = calls[0][1]["env"]
        assert env["AWS_ACCESS_KEY_ID"] == "AKIAEXAMPLE"
        assert env["AWS_SECRET_ACCESS_KEY"] == "secret"
        assert env["AWS_DEFAULT_REGION"] == "eu-west-1"

    def test_missing_source(self, monkeypatch, _tmp_logfile):
        from clouddump.job_s3 import run_s3_sync

        rc = run_s3_sync(self._cfg(source=""), _tmp_logfile)
        assert rc == 1

    def test_invalid_source_prefix(self, monkeypatch, _tmp_logfile):
        from clouddump.job_s3 import run_s3_sync

        rc = run_s3_sync(self._cfg(source="http://wrong"), _tmp_logfile)
        assert rc == 1

    def test_nonzero_exit(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_s3 import run_s3_sync

        dest = str(tmp_path / "s3out")
        _capture_cmd(monkeypatch, "clouddump.job_s3.run_cmd", rc=1)

        rc = run_s3_sync(self._cfg(destination=dest), _tmp_logfile)
        assert rc == 1

    def test_creates_destination_dir(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_s3 import run_s3_sync

        dest = str(tmp_path / "deep" / "nested" / "dir")
        _capture_cmd(monkeypatch, "clouddump.job_s3.run_cmd")

        run_s3_sync(self._cfg(destination=dest), _tmp_logfile)
        assert os.path.isdir(dest)


# ── Azure runner ────────────────────────────────────────────────────────────


class TestAzureRunner:
    """Tests for clouddump.job_azure.run_az_sync."""

    @staticmethod
    def _cfg(**overrides):
        base = {
            "source": "https://account.blob.core.windows.net/container?sv=2021&sig=abc",
            "destination": "/backup/azure",
        }
        base.update(overrides)
        return base

    def test_basic_sync(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_azure import run_az_sync

        dest = str(tmp_path / "azout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_azure.run_cmd")

        rc = run_az_sync(self._cfg(destination=dest), _tmp_logfile)

        assert rc == 0
        cmd = calls[0][0]
        assert cmd[0] == "azcopy"
        assert cmd[1] == "sync"
        assert "--recursive" in cmd
        assert "--delete-destination=true" in cmd

    def test_delete_disabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_azure import run_az_sync

        dest = str(tmp_path / "azout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_azure.run_cmd")

        run_az_sync(self._cfg(destination=dest, delete_destination=False), _tmp_logfile)

        assert "--delete-destination=false" in calls[0][0]

    def test_missing_source(self, monkeypatch, _tmp_logfile):
        from clouddump.job_azure import run_az_sync

        rc = run_az_sync(self._cfg(source=""), _tmp_logfile)
        assert rc == 1

    def test_invalid_source_prefix(self, monkeypatch, _tmp_logfile):
        from clouddump.job_azure import run_az_sync

        rc = run_az_sync(self._cfg(source="ftp://wrong"), _tmp_logfile)
        assert rc == 1

    def test_nonzero_exit(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_azure import run_az_sync

        dest = str(tmp_path / "azout")
        _capture_cmd(monkeypatch, "clouddump.job_azure.run_cmd", rc=1)

        rc = run_az_sync(self._cfg(destination=dest), _tmp_logfile)
        assert rc == 1

    def test_creates_destination_dir(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_azure import run_az_sync

        dest = str(tmp_path / "deep" / "nested")
        _capture_cmd(monkeypatch, "clouddump.job_azure.run_cmd")

        run_az_sync(self._cfg(destination=dest), _tmp_logfile)
        assert os.path.isdir(dest)


# ── PostgreSQL runner ──────────────────────────────────────────────────────


class TestPgSQLRunner:
    """Tests for clouddump.job_pgsql.run_pg_dump — db_retries configuration."""

    @staticmethod
    def _cfg(**overrides):
        base = {
            "host": "pg.example.com",
            "port": "5432",
            "user": "backupuser",
            "pass": "secret",
            "backuppath": "/backup/pgsql",
        }
        base.update(overrides)
        return base

    def test_custom_db_retries(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_pgsql import run_pg_dump

        dest = str(tmp_path / "pgout")
        attempts = []

        def fake_run_cmd(cmd, **kwargs):
            if cmd[0] == "psql":
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("testdb\n")
                return 0
            attempts.append(1)
            return 1  # pg_dump always fails

        monkeypatch.setattr("clouddump.job_pgsql.run_cmd", fake_run_cmd)
        monkeypatch.setattr("clouddump.job_pgsql.time.sleep", lambda _: None)

        rc = run_pg_dump(self._cfg(backuppath=dest, db_retries=2, compress=False), _tmp_logfile)
        assert rc == 1
        assert len(attempts) == 2  # exactly 2, not default 3

    def test_default_db_retries(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_pgsql import run_pg_dump

        dest = str(tmp_path / "pgout")
        attempts = []

        def fake_run_cmd(cmd, **kwargs):
            if cmd[0] == "psql":
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("testdb\n")
                return 0
            attempts.append(1)
            return 1

        monkeypatch.setattr("clouddump.job_pgsql.run_cmd", fake_run_cmd)
        monkeypatch.setattr("clouddump.job_pgsql.time.sleep", lambda _: None)

        rc = run_pg_dump(self._cfg(backuppath=dest, compress=False), _tmp_logfile)
        assert rc == 1
        assert len(attempts) == 3  # default

    def test_only_system_databases_returns_success(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_pgsql import run_pg_dump

        dest = str(tmp_path / "pgout")

        def fake_run_cmd(cmd, **kwargs):
            if cmd[0] == "psql":
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("postgres\ntemplate0\ntemplate1\n")
                return 0
            return 0

        monkeypatch.setattr("clouddump.job_pgsql.run_cmd", fake_run_cmd)

        rc = run_pg_dump(self._cfg(backuppath=dest, compress=False), _tmp_logfile)
        assert rc == 0


# ── MySQL runner ────────────────────────────────────────────────────────────


class TestMySQLRunner:
    """Tests for clouddump.job_mysql.run_mysql_dump."""

    @staticmethod
    def _cfg(**overrides):
        base = {
            "host": "mysql.example.com",
            "port": "3306",
            "user": "backupuser",
            "pass": "secret",
            "backuppath": "/backup/mysql",
        }
        base.update(overrides)
        return base

    @staticmethod
    def _fake_mysql_run_cmd(recorded=None):
        """Return a fake run_cmd that simulates mysql and mysqldump."""
        if recorded is None:
            recorded = []

        def fake(cmd, **kwargs):
            recorded.append((cmd, kwargs))
            if cmd[0] == "mysql":
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("testdb\n")
            elif cmd[0] == "mysqldump":
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("-- MySQL dump\nCREATE TABLE...\n")
            # bzip2 — just ignore
            return 0

        return fake, recorded

    def test_basic_dump(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_mysql import run_mysql_dump

        dest = str(tmp_path / "mysqlout")
        fake, recorded = self._fake_mysql_run_cmd()
        monkeypatch.setattr("clouddump.job_mysql.run_cmd", fake)

        rc = run_mysql_dump(self._cfg(backuppath=dest, compress=False), _tmp_logfile)

        assert rc == 0
        assert len(recorded) == 2
        assert recorded[0][0][0] == "mysql"
        assert recorded[1][0][0] == "mysqldump"
        cmd = recorded[1][0]
        assert "-h" in cmd
        assert "mysql.example.com" in cmd
        assert "--single-transaction" in cmd
        assert "--routines" in cmd
        assert "--triggers" in cmd
        assert "--events" in cmd

    def test_env_password(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_mysql import run_mysql_dump

        dest = str(tmp_path / "mysqlout")
        fake, recorded = self._fake_mysql_run_cmd()
        monkeypatch.setattr("clouddump.job_mysql.run_cmd", fake)

        run_mysql_dump(self._cfg(backuppath=dest, compress=False), _tmp_logfile)

        for _, call_kwargs in recorded:
            env = call_kwargs.get("env", {})
            assert env.get("MYSQL_PWD") == "secret"

    def test_excludes_system_databases(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_mysql import run_mysql_dump

        dest = str(tmp_path / "mysqlout")
        dumped = []

        def fake_run_cmd(cmd, **kwargs):
            if cmd[0] == "mysql":
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("information_schema\nperformance_schema\nsys\nuserdb\n")
            elif cmd[0] == "mysqldump":
                dumped.append(cmd[-1])
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("-- dump\n")
            return 0

        monkeypatch.setattr("clouddump.job_mysql.run_cmd", fake_run_cmd)

        run_mysql_dump(self._cfg(backuppath=dest, compress=False), _tmp_logfile)

        assert dumped == ["userdb"]

    def test_explicit_databases(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_mysql import run_mysql_dump

        dest = str(tmp_path / "mysqlout")
        dumped = []

        def fake_run_cmd(cmd, **kwargs):
            if cmd[0] == "mysql":
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("db1\ndb2\ndb3\n")
            elif cmd[0] == "mysqldump":
                dumped.append(cmd[-1])
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("-- dump\n")
            return 0

        monkeypatch.setattr("clouddump.job_mysql.run_cmd", fake_run_cmd)

        run_mysql_dump(self._cfg(backuppath=dest, compress=False, databases=["db1", "db3"]), _tmp_logfile)

        assert dumped == ["db1", "db3"]

    def test_missing_host(self, monkeypatch, _tmp_logfile):
        from clouddump.job_mysql import run_mysql_dump

        rc = run_mysql_dump(self._cfg(host=""), _tmp_logfile)
        assert rc == 1

    def test_missing_password(self, monkeypatch, _tmp_logfile):
        from clouddump.job_mysql import run_mysql_dump

        rc = run_mysql_dump(self._cfg(**{"pass": ""}), _tmp_logfile)
        assert rc == 1

    def test_nonzero_exit(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_mysql import run_mysql_dump

        dest = str(tmp_path / "mysqlout")

        def fake_run_cmd(cmd, **kwargs):
            if cmd[0] == "mysql":
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("testdb\n")
                return 0
            return 1  # mysqldump fails

        monkeypatch.setattr("clouddump.job_mysql.run_cmd", fake_run_cmd)
        monkeypatch.setattr("clouddump.job_mysql.time.sleep", lambda _: None)

        rc = run_mysql_dump(self._cfg(backuppath=dest), _tmp_logfile)
        assert rc == 1

    def test_custom_db_retries(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_mysql import run_mysql_dump

        dest = str(tmp_path / "mysqlout")
        attempts = []

        def fake_run_cmd(cmd, **kwargs):
            if cmd[0] == "mysql":
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("testdb\n")
                return 0
            attempts.append(1)
            return 1  # mysqldump always fails

        monkeypatch.setattr("clouddump.job_mysql.run_cmd", fake_run_cmd)
        monkeypatch.setattr("clouddump.job_mysql.time.sleep", lambda _: None)

        rc = run_mysql_dump(self._cfg(backuppath=dest, db_retries=2), _tmp_logfile)
        assert rc == 1
        assert len(attempts) == 2  # exactly 2, not default 3

    def test_creates_destination_dir(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_mysql import run_mysql_dump

        dest = str(tmp_path / "deep" / "nested")
        fake, _ = self._fake_mysql_run_cmd()
        monkeypatch.setattr("clouddump.job_mysql.run_cmd", fake)

        run_mysql_dump(self._cfg(backuppath=dest, compress=False), _tmp_logfile)
        assert os.path.isdir(dest)

    def test_only_system_databases_returns_success(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_mysql import run_mysql_dump

        dest = str(tmp_path / "mysqlout")

        def fake_run_cmd(cmd, **kwargs):
            if cmd[0] == "mysql":
                stdout = kwargs.get("stdout")
                if stdout:
                    stdout.write("information_schema\nmysql\nperformance_schema\nsys\n")
                return 0
            return 0

        monkeypatch.setattr("clouddump.job_mysql.run_cmd", fake_run_cmd)

        rc = run_mysql_dump(self._cfg(backuppath=dest, compress=False), _tmp_logfile)
        assert rc == 0


# ── GitHub runner ───────────────────────────────────────────────────────────


class TestGitHubRunner:
    """Tests for clouddump.job_github.run_github_backup."""

    @staticmethod
    def _cfg(**overrides):
        base = {
            "name": "my-org",
            "destination": "/backup/github",
            "token": "ghp_testtoken123",
        }
        base.update(overrides)
        return base

    def test_default_flags(self, monkeypatch, tmp_path, _tmp_logfile):
        """Only repos enabled by default; metadata flags off."""
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        rc = run_github_backup(self._cfg(destination=dest), _tmp_logfile)

        assert rc == 0
        cmd = calls[0][0]
        assert cmd[0] == "github-backup"
        assert cmd[1] == "my-org"
        assert "--token" in cmd
        assert "--organization" in cmd
        assert "--output-directory" in cmd
        # Defaults on
        assert "--repositories" in cmd
        assert "--bare" in cmd
        # Defaults off (metadata)
        assert "--issues" not in cmd
        assert "--issue-comments" not in cmd
        assert "--pulls" not in cmd
        assert "--pull-comments" not in cmd
        assert "--labels" not in cmd
        assert "--milestones" not in cmd
        assert "--releases" not in cmd
        assert "--assets" not in cmd
        assert "--wikis" not in cmd
        assert "--fork" not in cmd
        assert "--lfs" not in cmd
        assert "--skip-archived" not in cmd

    def test_repositories_filter(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest, repositories=["repo-a", "repo-b"]), _tmp_logfile)

        cmd = calls[0][0]
        assert "--repository" in cmd
        idx = cmd.index("--repository")
        assert cmd[idx + 1] == "repo-a"
        idx2 = cmd.index("--repository", idx + 1)
        assert cmd[idx2 + 1] == "repo-b"

    def test_repositories_default_all(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest), _tmp_logfile)

        cmd = calls[0][0]
        assert "--repository" not in cmd

    def test_repos_disabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest, include_repos=False), _tmp_logfile)

        cmd = calls[0][0]
        assert "--repositories" not in cmd
        assert "--bare" not in cmd

    def test_issues_disabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest, include_issues=False), _tmp_logfile)

        cmd = calls[0][0]
        assert "--issues" not in cmd
        assert "--issue-comments" not in cmd
        assert "--issue-events" not in cmd

    def test_pulls_disabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest, include_pulls=False), _tmp_logfile)

        cmd = calls[0][0]
        assert "--pulls" not in cmd
        assert "--pull-comments" not in cmd
        assert "--pull-commits" not in cmd
        assert "--pull-details" not in cmd

    def test_labels_disabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest, include_labels=False), _tmp_logfile)

        assert "--labels" not in calls[0][0]

    def test_milestones_disabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest, include_milestones=False), _tmp_logfile)

        assert "--milestones" not in calls[0][0]

    def test_releases_disabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest, include_releases=False), _tmp_logfile)

        cmd = calls[0][0]
        assert "--releases" not in cmd
        assert "--assets" not in cmd

    def test_forks_enabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest, include_forks=True), _tmp_logfile)

        assert "--fork" in calls[0][0]

    def test_archived_disabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest, include_archived=False), _tmp_logfile)

        assert "--skip-archived" in calls[0][0]

    def test_lfs_enabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest, include_lfs=True), _tmp_logfile)

        assert "--lfs" in calls[0][0]

    def test_everything_disabled(self, monkeypatch, tmp_path, _tmp_logfile):
        """Minimal backup with all optional content turned off."""
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(
            destination=dest,
            include_repos=False,
            include_issues=False,
            include_pulls=False,
            include_labels=False,
            include_milestones=False,
            include_releases=False,
            include_wikis=False,
        ), _tmp_logfile)

        cmd = calls[0][0]
        # Core flags still present
        assert "--organization" in cmd
        assert "--incremental" in cmd
        assert "--private" in cmd
        # Everything optional gone
        for flag in ("--repositories", "--bare", "--issues", "--pulls",
                     "--labels", "--milestones", "--releases", "--assets", "--wikis"):
            assert flag not in cmd

    def test_missing_name(self, monkeypatch, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        rc = run_github_backup(self._cfg(name=""), _tmp_logfile)
        assert rc == 1

    def test_missing_destination(self, monkeypatch, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        rc = run_github_backup(self._cfg(destination=""), _tmp_logfile)
        assert rc == 1

    def test_missing_token(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        rc = run_github_backup(self._cfg(token=""), _tmp_logfile)
        assert rc == 1

    def test_nonzero_exit(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "ghout")
        _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd", rc=1)

        rc = run_github_backup(self._cfg(destination=dest), _tmp_logfile)
        assert rc == 1

    def test_creates_destination_dir(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_github import run_github_backup

        dest = str(tmp_path / "deep" / "nested")
        _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        run_github_backup(self._cfg(destination=dest), _tmp_logfile)
        assert os.path.isdir(dest)

    def test_token_not_in_log(self, monkeypatch, tmp_path, _tmp_logfile):
        """Token must not leak into debug output."""
        import logging
        from clouddump.job_github import run_github_backup
        import clouddump

        dest = str(tmp_path / "ghout")
        _capture_cmd(monkeypatch, "clouddump.job_github.run_cmd")

        # Capture log output
        log_records = []
        handler = logging.Handler()
        handler.emit = lambda record: log_records.append(record.getMessage())
        clouddump.log.addHandler(handler)
        clouddump.log.setLevel("DEBUG")
        try:
            run_github_backup(self._cfg(destination=dest), _tmp_logfile)
        finally:
            clouddump.log.setLevel("INFO")
            clouddump.log.removeHandler(handler)

        log_text = "\n".join(log_records)
        assert "ghp_testtoken123" not in log_text
        assert "REDACTED" in log_text


# ── Rsync runner ───────────────────────────────────────────────────────────


class TestRsyncRunner:
    """Tests for clouddump.job_rsync.run_rsync_sync."""

    @staticmethod
    def _cfg(**overrides):
        base = {
            "source": "user@server.example.com:/data/important",
            "destination": "/backup/rsync",
            "ssh_key": "/config/id_ed25519",
        }
        base.update(overrides)
        return base

    def test_basic_sync(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd")

        rc = run_rsync_sync(self._cfg(destination=dest), _tmp_logfile)

        assert rc == 0
        assert len(calls) == 1
        cmd = calls[0][0]
        assert cmd[0] == "rsync"
        assert "-az" in cmd
        assert "--delete" in cmd
        assert "user@server.example.com:/data/important" in cmd
        assert dest in cmd

    def test_ssh_options(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd")

        run_rsync_sync(self._cfg(destination=dest, ssh_port="2222"), _tmp_logfile)

        cmd = calls[0][0]
        idx = cmd.index("-e")
        ssh_cmd = cmd[idx + 1]
        assert "-i /config/id_ed25519" in ssh_cmd
        assert "-p 2222" in ssh_cmd
        assert "StrictHostKeyChecking=accept-new" in ssh_cmd
        assert "BatchMode=yes" in ssh_cmd

    def test_default_ssh_port(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd")

        run_rsync_sync(self._cfg(destination=dest), _tmp_logfile)

        cmd = calls[0][0]
        idx = cmd.index("-e")
        ssh_cmd = cmd[idx + 1]
        assert "-p 22" in ssh_cmd

    def test_delete_disabled(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd")

        run_rsync_sync(self._cfg(destination=dest, delete_destination=False), _tmp_logfile)

        assert "--delete" not in calls[0][0]

    def test_exclude_patterns(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd")

        run_rsync_sync(self._cfg(destination=dest, exclude=["*.tmp", "cache/"]), _tmp_logfile)

        cmd = calls[0][0]
        idx1 = cmd.index("--exclude")
        assert cmd[idx1 + 1] == "*.tmp"
        idx2 = cmd.index("--exclude", idx1 + 1)
        assert cmd[idx2 + 1] == "cache/"

    def test_missing_source(self, monkeypatch, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        rc = run_rsync_sync(self._cfg(source=""), _tmp_logfile)
        assert rc == 1

    def test_invalid_source_no_colon(self, monkeypatch, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        rc = run_rsync_sync(self._cfg(source="/local/path/only"), _tmp_logfile)
        assert rc == 1

    def test_missing_ssh_key(self, monkeypatch, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        rc = run_rsync_sync(self._cfg(ssh_key=""), _tmp_logfile)
        assert rc == 1

    def test_nonzero_exit(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")
        _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd", rc=1)

        rc = run_rsync_sync(self._cfg(destination=dest), _tmp_logfile)
        assert rc == 1

    def test_creates_destination_dir(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "deep" / "nested" / "dir")
        _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd")

        run_rsync_sync(self._cfg(destination=dest), _tmp_logfile)
        assert os.path.isdir(dest)

    def test_min_age_days_builds_filelist(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd")

        # Stub _find_old_files to return a known file list
        monkeypatch.setattr(
            "clouddump.job_rsync._find_old_files",
            lambda *a, **kw: ["old/file1.txt", "old/file2.log"],
        )

        rc = run_rsync_sync(self._cfg(destination=dest, min_age_days=30), _tmp_logfile)

        assert rc == 0
        cmd = calls[0][0]
        assert "--files-from" in cmd
        # delete_destination defaults to True — mirror mode
        assert "--delete" in cmd

    def test_min_age_days_no_delete(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd")

        monkeypatch.setattr(
            "clouddump.job_rsync._find_old_files",
            lambda *a, **kw: ["old/file.txt"],
        )

        run_rsync_sync(self._cfg(destination=dest, min_age_days=30, delete_destination=False), _tmp_logfile)

        cmd = calls[0][0]
        assert "--files-from" in cmd
        assert "--delete" not in cmd

    def test_min_age_days_no_files_returns_0(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd")

        monkeypatch.setattr(
            "clouddump.job_rsync._find_old_files",
            lambda *a, **kw: [],
        )

        rc = run_rsync_sync(self._cfg(destination=dest, min_age_days=7), _tmp_logfile)

        assert rc == 0
        assert len(calls) == 0  # rsync should not have been called

    def test_min_age_days_find_failure(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")

        monkeypatch.setattr(
            "clouddump.job_rsync._find_old_files",
            lambda *a, **kw: None,  # signals failure
        )

        rc = run_rsync_sync(self._cfg(destination=dest, min_age_days=7), _tmp_logfile)
        assert rc == 1

    def test_min_age_days_cleans_up_tempfile(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd")

        monkeypatch.setattr(
            "clouddump.job_rsync._find_old_files",
            lambda *a, **kw: ["somefile.txt"],
        )

        run_rsync_sync(self._cfg(destination=dest, min_age_days=5), _tmp_logfile)

        # The temp file referenced in --files-from should be cleaned up
        cmd = calls[0][0]
        idx = cmd.index("--files-from")
        filelist_path = cmd[idx + 1]
        assert not os.path.exists(filelist_path)

    def test_without_min_age_no_files_from(self, monkeypatch, tmp_path, _tmp_logfile):
        """Without min_age_days, rsync runs normally (no --files-from)."""
        from clouddump.job_rsync import run_rsync_sync

        dest = str(tmp_path / "rsyncout")
        calls = _capture_cmd(monkeypatch, "clouddump.job_rsync.run_cmd")

        run_rsync_sync(self._cfg(destination=dest), _tmp_logfile)

        cmd = calls[0][0]
        assert "--files-from" not in cmd
        assert "--delete" in cmd


# ── Job dispatch ────────────────────────────────────────────────────────────


class TestJobDispatch:
    """Tests for clouddump.jobs.execute_job — routing and multi-target handling."""

    def test_unknown_type_returns_1(self, _tmp_logfile):
        from clouddump.jobs import execute_job

        rc = execute_job({"type": "nonexistent"}, _tmp_logfile)
        assert rc == 1

    def test_empty_targets_returns_1(self, _tmp_logfile):
        from clouddump.jobs import execute_job

        rc = execute_job({"type": "s3bucket", "buckets": []}, _tmp_logfile)
        assert rc == 1

    def test_dispatches_to_s3(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.jobs import execute_job
        from clouddump import jobs

        called_with = []
        monkeypatch.setattr(jobs, "_RUNNERS", {
            "s3bucket": ("buckets", lambda target, lf: (called_with.append(target), 0)[1]),
        })

        job = {"type": "s3bucket", "buckets": [{"source": "s3://b"}]}
        rc = execute_job(job, _tmp_logfile)

        assert rc == 0
        assert called_with == [{"source": "s3://b"}]

    def test_dispatches_to_github(self, monkeypatch, tmp_path, _tmp_logfile):
        from clouddump.jobs import execute_job
        from clouddump import jobs

        called_with = []
        monkeypatch.setattr(jobs, "_RUNNERS", {
            "github": ("organizations", lambda target, lf: (called_with.append(target), 0)[1]),
        })

        job = {"type": "github", "organizations": [{"name": "org1"}]}
        rc = execute_job(job, _tmp_logfile)

        assert rc == 0
        assert called_with == [{"name": "org1"}]

    def test_multiple_targets_all_attempted(self, monkeypatch, _tmp_logfile):
        """Even if target 1 fails, target 2 should still run."""
        from clouddump.jobs import execute_job
        from clouddump import jobs

        results = iter([1, 0])
        called = []

        def fake_runner(target, lf):
            called.append(target["id"])
            return next(results)

        monkeypatch.setattr(jobs, "_RUNNERS", {
            "s3bucket": ("buckets", fake_runner),
        })

        job = {"type": "s3bucket", "buckets": [{"id": "a"}, {"id": "b"}]}
        rc = execute_job(job, _tmp_logfile)

        assert rc == 1  # worst exit code
        assert called == ["a", "b"]  # both attempted

    def test_multiple_targets_all_succeed(self, monkeypatch, _tmp_logfile):
        from clouddump.jobs import execute_job
        from clouddump import jobs

        monkeypatch.setattr(jobs, "_RUNNERS", {
            "s3bucket": ("buckets", lambda t, lf: 0),
        })

        job = {"type": "s3bucket", "buckets": [{"id": "a"}, {"id": "b"}]}
        rc = execute_job(job, _tmp_logfile)
        assert rc == 0
