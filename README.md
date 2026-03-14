# CloudDump 🌩️

[![CI](https://github.com/ralftar/CloudDump/actions/workflows/ci.yml/badge.svg)](https://github.com/ralftar/CloudDump/actions/workflows/ci.yml)
[![Publish](https://github.com/ralftar/CloudDump/actions/workflows/publish.yml/badge.svg)](https://github.com/ralftar/CloudDump/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Keep a copy of your cloud data somewhere you control.**

The cloud is just someone else's computer. CloudDump pulls your persistent
data — S3 buckets, Azure Blob Storage, PostgreSQL databases, MySQL
databases, GitHub organizations — down to on-premises storage, another
cloud, or wherever you want. On a schedule, unattended, with email
notifications when things succeed or fail.

## Supported sources

| Source | Job type | Tool used | Auth |
|--------|----------|-----------|------|
| AWS S3 | `s3bucket` | AWS CLI | Access key + secret |
| S3-compatible (MinIO, etc.) | `s3bucket` | AWS CLI | Access key + secret + `endpoint_url` |
| Azure Blob Storage | `azstorage` | AzCopy | SAS token in source URL |
| PostgreSQL | `pgsql` | pg_dump / psql | Host, port, user, password |
| MySQL / MariaDB | `mysql` | mysqldump / mysql | Host, port, user, password |
| GitHub organization | `github` | github-backup | Personal access token |

## Features

- **Cron scheduling** — standard 5-field cron patterns (`0 3 * * *`, `*/15 * * * *`)
- **Catch-up execution** — if a scheduled run is missed because another job is
  still running, it fires as soon as the slot opens (within a 60-minute window)
- **Retry & timeout** — configurable per job (default: 3 attempts, 1-week timeout)
- **Email reports** — success/failure notifications with log file attached
- **Mount support** — SSH (`sshfs`) and SMB (`smbnetfs`) destinations without
  elevated privileges
- **Credential redaction** — passwords, keys, and tokens are scrubbed from logs
  and emails automatically
- **Health check** — built-in Docker `HEALTHCHECK` via heartbeat file
- **Graceful shutdown** — SIGTERM forwarded to child processes

## Not a backup system

CloudDump is **not** a backup system. There is no rotation, no versioning,
no retention policies. It gives you a current-state copy of your data,
synced on a cron schedule. What you do with that copy — feed it into
Restic, Borg, Veeam, tape, a RAID array in your basement — is up to you.

## Why

You store data in S3 or Azure. Your databases run in the cloud. That's
fine — until a provider has an outage, a misconfigured IAM policy deletes
your bucket, or you just want to sleep better knowing there's a copy on
hardware you own.

CloudDump runs as a single Docker container. Point it at your cloud
resources, tell it when to sync, and forget about it. If something breaks,
you get an email.

## Disaster recovery

CloudDump can be a key component in your disaster recovery plan. Critically,
it *pulls* data from the cloud — the cloud provider has no knowledge of your
local copy. This means a compromised or malfunctioning cloud environment
cannot delete, encrypt, or tamper with data it doesn't know exists. The
dependency flows one way: your copy depends on the cloud being reachable,
but the cloud has zero control over what you already have.

A typical DR setup:

1. **CloudDump** syncs cloud data to local storage on a schedule.
2. **A backup tool** (Restic, Borg, Veeam, etc.) snapshots the local copy
   with versioning and retention.
3. **A dead-man switch** (e.g. [Healthchecks.io](https://healthchecks.io))
   alerts you when expected emails *stop arriving* — a silent failure is
   worse than a loud one.
4. **Regular restore drills** — periodically verify that you can actually
   restore from the local copy.

## Architecture

CloudDump is a single-process Python application in a Debian 12 container.

```
config.json ──> [Orchestrator] ──> aws s3 sync
                     │          ──> azcopy sync
                     │          ──> pg_dump / psql
                     │          ──> mysqldump
                     │          ──> github-backup
                     │
                     ├── cron scheduler (check every 60s)
                     ├── sequential job execution
                     ├── signal forwarding (SIGTERM → child)
                     └── email reports (SMTPS)
```

Jobs run one at a time. If job B is scheduled while job A is still running,
job B fires as soon as A finishes (within a 60-minute catch-up window). This
keeps resource usage predictable and avoids conflicts on shared destinations.

### Bundled tools

| Tool | Source | Update mechanism |
|------|--------|-----------------|
| AWS CLI | Debian apt (v1) | Debian base image |
| AzCopy | Microsoft apt repo | Debian base image |
| PostgreSQL client | Debian apt (v15) | Manual (pinned to major version in Dockerfile) |
| MySQL client | Debian apt (default-mysql-client) | Debian base image |
| github-backup | pip (requirements.txt) | Dependabot (pip) |

### Dependency update strategy

Dependabot manages three ecosystems: GitHub Actions, the Debian base image
(`docker`), and Python packages (`pip`). When Dependabot bumps the Debian
tag (e.g. `12.13-slim` → `12.14-slim`), the image rebuilds from scratch
and `apt-get upgrade -y` pulls the latest versions of all apt-managed
tools (AWS CLI, AzCopy, PostgreSQL client, git, etc.).

Between Debian releases, apt-managed tool versions stay fixed. This is
intentional — it keeps the image deterministic and avoids surprise
breakage from mid-cycle package updates.

**Note:** The PostgreSQL client is pinned to a major version in the
Dockerfile (`postgresql-client-15`). Unlike the other apt packages, it
does not auto-update with Debian base image bumps. When your PostgreSQL
servers move to a new major version, update the Dockerfile manually.

## Quick start

**1. Create a config file** (see [Configuration reference](docs/configuration.md) for all options)

```json
{
  "settings": {
    "HOST": "myserver",
    "SMTPSERVER": "smtp.example.com",
    "SMTPPORT": "465",
    "SMTPUSER": "alerts@example.com",
    "SMTPPASS": "smtp-password",
    "MAILFROM": "alerts@example.com",
    "MAILTO": "ops@example.com, oncall@example.com"
  },
  "jobs": [
    {
      "type": "s3bucket",
      "id": "prod-assets",
      "crontab": "0 3 * * *",
      "buckets": [
        {
          "source": "s3://my-bucket",
          "destination": "/mnt/clouddump/s3",
          "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
          "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
          "aws_region": "eu-west-1"
        }
      ]
    }
  ]
}
```

**2. Run the container**

```sh
docker run -d --restart always \
  --name clouddump \
  --mount type=bind,source=$(pwd)/config.json,target=/config/config.json,readonly \
  --volume /backup:/mnt/clouddump \
  ghcr.io/ralftar/clouddump:latest
```

That's it. CloudDump will sync your S3 bucket to `/backup/s3` every day at
03:00 and email you the result.

## Troubleshooting

**Container won't start** — Verify `config.json` is valid JSON and mounted at
`/config/config.json`. CloudDump validates all jobs at startup and logs errors
to stdout.

**Jobs not running** — Check your cron syntax. Supported: `*`, `*/N`, exact
values. Not supported: ranges (`1-5`), lists (`1,3,5`). Check container logs
for scheduling messages.

**Email not working** — CloudDump uses SMTPS (SSL on port 465). Verify the
container can reach your SMTP server. Check logs for `Failed to send email`
messages.

**Mount failures** — SSH mounts need a valid key and reachable host. SMB mounts
need FUSE support in the container runtime. Check logs for mount errors at
startup.

**Debug mode** — Set `"DEBUG": true` in settings for verbose logging.

## Contributing

Contributions are welcome. Please open an issue first to discuss what you'd
like to change.

## License

[MIT](LICENSE) — Copyright (c) 2026 Ralf Bjarne Taraldset
