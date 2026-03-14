"""GitHub organization backup job runner."""

import os
import time

from clouddump import cfg, log, redact, run_cmd


def run_github_backup(org, logfile_path):
    """Back up a single GitHub organization using ``github-backup``."""
    name = cfg(org, "name")
    destination = cfg(org, "destination")
    token = cfg(org, "token")

    if not name or not destination:
        log.error("Missing name or destination for GitHub organization.")
        return 1

    if not token:
        log.error("Missing token for GitHub organization %s.", name)
        return 1

    os.makedirs(destination, exist_ok=True)

    log.debug("Organization: %s", name)
    log.debug("Destination: %s", destination)
    log.debug("Token: %s", redact(f"token={token}"))
    log.debug("Backing up organization %s to %s...", name, destination)

    def _enabled(key, default="true"):
        return str(cfg(org, key, default)).lower() == "true"

    cmd = [
        "github-backup", name,
        "--token", token,
        "--organization",
        "--output-directory", destination,
        "--incremental",
        "--private",
        "--log-level", "info",
        "--throttle-limit", "5000",
        "--throttle-pause", "0.72",
    ]

    if _enabled("include_repos"):
        cmd.append("--repositories")
        cmd.append("--bare")

    if _enabled("include_issues"):
        cmd += ["--issues", "--issue-comments", "--issue-events"]

    if _enabled("include_pulls"):
        cmd += ["--pulls", "--pull-comments", "--pull-commits", "--pull-details"]

    if _enabled("include_labels"):
        cmd.append("--labels")

    if _enabled("include_milestones"):
        cmd.append("--milestones")

    if _enabled("include_releases"):
        cmd += ["--releases", "--assets"]

    if _enabled("include_wikis"):
        cmd.append("--wikis")

    if _enabled("include_forks", "false"):
        cmd.append("--fork")

    if not _enabled("include_archived"):
        cmd.append("--skip-archived")

    if _enabled("include_lfs", "false"):
        cmd.append("--lfs")

    t0 = time.time()
    with open(logfile_path, "a") as logf:
        rc = run_cmd(cmd, stdout=logf, stderr=logf)
    elapsed = int(time.time() - t0)

    if rc != 0:
        log.error("GitHub backup failed after %d seconds.", elapsed)
    else:
        log.debug("GitHub backup completed successfully in %d seconds.", elapsed)
    return rc
