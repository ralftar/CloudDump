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

    log.info("Account: %s (type: %s)", name, account_type)
    log.info("Destination: %s", destination)
    log.debug("Token: %s", redact(f"token={token}"))
    log.info("Backing up %s %s...", account_type, name)

    def _enabled(key, default="true"):
        return str(cfg(org, key, default)).lower() == "true"

    # Write token to a temp file to keep it out of process arguments
    # (visible via ps aux). github-backup doesn't support env vars.
    fd, token_path = tempfile.mkstemp(prefix="gh-token-")
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

    if _enabled("include_repos"):
        cmd.append("--repositories")
        cmd.append("--bare")

    if _enabled("include_issues", "false"):
        cmd += ["--issues", "--issue-comments", "--issue-events"]

    if _enabled("include_pulls", "false"):
        cmd += ["--pulls", "--pull-comments", "--pull-commits", "--pull-details"]

    if _enabled("include_labels", "false"):
        cmd.append("--labels")

    if _enabled("include_milestones", "false"):
        cmd.append("--milestones")

    if _enabled("include_releases", "false"):
        cmd += ["--releases", "--assets"]

    if _enabled("include_wikis", "false"):
        cmd.append("--wikis")

    if _enabled("include_forks", "false"):
        cmd.append("--fork")

    if not _enabled("include_archived"):
        cmd.append("--skip-archived")

    if _enabled("include_lfs", "false"):
        cmd.append("--lfs")

    t0 = time.time()
    try:
        rc = run_cmd(cmd, logfile_path=logfile_path)
    finally:
        _safe_remove(token_path)
    elapsed = int(time.time() - t0)

    if rc != 0:
        log.error("GitHub backup of %s '%s' failed after %ds.", account_type, name, elapsed)
    else:
        log.info("GitHub backup of %s '%s' completed in %ds.", account_type, name, elapsed)
    return rc
