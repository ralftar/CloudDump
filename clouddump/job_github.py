"""GitHub backup job runner (organizations and user accounts)."""

import os
import tempfile
import time

from clouddump import cfg, log, redact, run_cmd, _safe_remove


def run_github_backup(org, logfile_path):
    """Back up a GitHub organization or user account using ``github-backup``."""
    name = cfg(org, "name")
    destination = cfg(org, "destination")
    token = cfg(org, "token")
    account_type = cfg(org, "account_type", "org")

    if not name or not destination:
        log.error("Missing name or destination for GitHub account.")
        return 1

    if not token:
        log.error("Missing token for GitHub account %s.", name)
        return 1

    os.makedirs(destination, exist_ok=True)

    log.info("Backing up GitHub account", extra={"account": name, "account_type": account_type, "destination": destination})
    log.debug("Token: %s", redact(f"token={token}"))

    # Write token to a temp file to keep it out of process arguments
    # (visible via ps aux). github-backup doesn't support env vars.
    fd, token_path = tempfile.mkstemp(prefix="gh-token-", dir=destination)
    os.chmod(token_path, 0o600)
    os.write(fd, token.encode())
    os.close(fd)

    cmd = [
        "github-backup", name,
        "--token", f"file://{token_path}",
        "--output-directory", destination,
        "--incremental",
        "--private",
        "--log-level", "info",
        "--throttle-limit", "5000",
        "--throttle-pause", "0.72",
    ]

    if account_type == "org":
        cmd.append("--organization")

    repos = cfg(org, "repositories", [])
    for repo in repos:
        cmd += ["--repository", repo]

    if cfg(org, "include_repos", True):
        cmd.append("--repositories")
        cmd.append("--bare")

    if cfg(org, "include_issues", False):
        cmd += ["--issues", "--issue-comments", "--issue-events"]

    if cfg(org, "include_pulls", False):
        cmd += ["--pulls", "--pull-comments", "--pull-commits", "--pull-details"]

    if cfg(org, "include_labels", False):
        cmd.append("--labels")

    if cfg(org, "include_milestones", False):
        cmd.append("--milestones")

    if cfg(org, "include_releases", False):
        cmd += ["--releases", "--assets"]

    if cfg(org, "include_wikis", False):
        cmd.append("--wikis")

    if cfg(org, "include_forks", False):
        cmd.append("--fork")

    if not cfg(org, "include_archived", True):
        cmd.append("--skip-archived")

    if cfg(org, "include_lfs", False):
        cmd.append("--lfs")

    t0 = time.time()
    try:
        rc = run_cmd(cmd, logfile_path=logfile_path)
    finally:
        _safe_remove(token_path)
    elapsed = int(time.time() - t0)

    if rc != 0:
        log.error("GitHub backup failed", extra={"account": name, "account_type": account_type, "elapsed_s": elapsed})
    else:
        log.info("GitHub backup completed", extra={"account": name, "account_type": account_type, "elapsed_s": elapsed})
    return rc
