# CloudDump 

[![CI](https://github.com/ralftar/CloudDump/actions/workflows/ci.yml/badge.svg)](https://github.com/ralftar/CloudDump/actions/workflows/ci.yml)
[![Publish](https://github.com/ralftar/CloudDump/actions/workflows/publish.yml/badge.svg)](https://github.com/ralftar/CloudDump/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**An independent copy of your cloud data. Under your control.**

![CloudDump](cloud_dump_illustration.png)

The cloud is just someone else's computer. CloudDump pulls your persistent
data — S3 buckets, Azure Blob Storage, PostgreSQL databases, MySQL
databases, GitHub repos — to an offsite location the cloud provider knows
nothing about. On a schedule, unattended, with email notifications when
things succeed or fail.

## Why

You store data in S3 or Azure. Your databases run in the cloud. That's
fine — until a provider has an outage, a misconfigured IAM policy deletes
your bucket, or you just want to sleep better knowing there's a copy
somewhere else — another cloud, a VPS, a NAS, wherever.

CloudDump runs as a single Docker container. Point it at your cloud
resources, tell it when to sync, and forget about it. If something breaks,
you get an email.

## Supported sources

| Source | Job type | Tool used | Auth |
|--------|----------|-----------|------|
| AWS S3 / S3-compatible (MinIO, etc.) | `s3bucket` | [AWS CLI](https://aws.amazon.com/cli/) | Access key + secret |
| Azure Blob Storage | `azstorage` | [AzCopy](https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azcopy-v10) | SAS token in source URL |
| PostgreSQL | `pgsql` | [pg_dump / psql](https://www.postgresql.org/docs/current/app-pgdump.html) | Host, port, user, password |
| MySQL / MariaDB | `mysql` | [mysqldump / mysql](https://dev.mysql.com/doc/refman/en/mysqldump.html) | Host, port, user, password |
| GitHub (org or user) | `github` | [github-backup](https://github.com/josegonzalez/python-github-backup) | Personal access token |
| Remote server (SSH) | `rsync` | [rsync](https://rsync.samba.org/) | SSH private key |

## Not a backup system

CloudDump is **not** a backup system. There is no rotation, no versioning,
no retention policies. It gives you a current-state copy of your data,
synced on a cron schedule. What you do with that copy — feed it into
Restic, Borg, Veeam, tape, a RAID array in your basement — is up to you.

## Features

- **Cron scheduling** — standard 5-field cron expressions (`0 3 * * *`, `*/15 * * * *`,
  `0 9-17 * * 1-5`, `0,30 * * * *`)
- **Retry & timeout** — configurable per job (default: 3 attempts, 1-week timeout)
- **Email reports** — success/failure notifications with log file attached
  (SSL, STARTTLS, or plain SMTP)
- **No special privileges** — runs as non-root, writes to `/backup` and `/mnt`
- **Credential redaction** — passwords, keys, tokens, PEM keys, and connection strings
  are scrubbed from all log output, logfiles, and emails automatically
- **Health endpoint** — `GET /healthz` returns last-run status as JSON (port configurable)
- **Run now** — send `SIGUSR1` to trigger all jobs immediately
- **Graceful shutdown** — SIGTERM forwarded to child processes

## Disaster recovery

CloudDump can be a key component in your disaster recovery plan. Critically,
it *pulls* data from the cloud — the cloud provider has no knowledge of your
local copy. This means a compromised or malfunctioning cloud environment
cannot delete, encrypt, or tamper with data it doesn't know exists. The
dependency flows one way: your copy depends on the cloud being reachable,
but the cloud has zero control over what you already have.

A typical DR setup:

1. **CloudDump** syncs cloud data to offsite storage on a schedule.
2. **A backup tool** (Restic, Borg, Veeam, etc.) snapshots that copy
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
                     │          ──> rsync
                     │
                     ├── cron scheduler (check every 60s)
                     ├── sequential job execution
                     ├── signal forwarding (SIGTERM → child)
                     └── email reports (SMTPS)
```

All jobs share a single top-level `crontab`. When the schedule triggers,
every job runs in sequence — in config order. No jobs are skipped.
If you need different schedules or parallel execution, run multiple
CloudDump instances with separate configurations.

### Bundled tools

| Tool | Source | Update mechanism |
|------|--------|-----------------|
| [AWS CLI](https://aws.amazon.com/cli/) | Debian apt (v1) | Debian base image |
| [AzCopy](https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azcopy-v10) | Microsoft apt repo | Debian base image |
| [PostgreSQL client](https://www.postgresql.org/docs/current/app-pgdump.html) | Debian apt (v15) | Manual (pinned to major version in Dockerfile) |
| [MySQL client](https://dev.mysql.com/doc/refman/en/mysqldump.html) | Debian apt (default-mysql-client) | Debian base image |
| [github-backup](https://github.com/josegonzalez/python-github-backup) | pip (requirements.txt) | Dependabot (pip) |

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

## Installation

CloudDump is distributed as a Docker image from GitHub Container Registry:

```
ghcr.io/ralftar/clouddump:latest
```

Pin to a specific version for production use:

```
ghcr.io/ralftar/clouddump:v0.9.0
```

No other installation is needed. The image includes all bundled tools
(AWS CLI, AzCopy, pg_dump, mysqldump, github-backup, rsync).

## Quick start

**1. Create a config file** (see [Configuration reference](CONFIGURATION.md) for all options, [JSON Schema](config.schema.json) for editor validation)

```json
{
  "host": "myserver",
  "smtp_server": "smtp.example.com",
  "smtp_port": "465",
  "smtp_user": "alerts@example.com",
  "smtp_pass": "smtp-password",
  "mail_from": "alerts@example.com",
  "mail_to": "ops@example.com, oncall@example.com",
  "crontab": "0 3 * * *",
  "jobs": [
    {
      "type": "s3bucket",
      "id": "prod-assets",
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
  -p 8080:8080 \
  --mount type=bind,source=$(pwd)/config.json,target=/config/config.json,readonly \
  --volume /backup:/mnt/clouddump \
  ghcr.io/ralftar/clouddump:latest
```

That's it. CloudDump will sync your S3 bucket to `/backup/s3` every day at
03:00 and email you the result.

**3. Trigger jobs manually** (optional — skip the cron wait)

```sh
docker kill -s USR1 clouddump
```

## Troubleshooting

**Container won't start** — Verify `config.json` is valid JSON and mounted at
`/config/config.json`. CloudDump validates all jobs at startup and logs errors
to stdout.

**Jobs not running** — Check your cron syntax (standard 5-field cron: `*`,
`*/N`, exact values, ranges `1-5`, lists `1,3,5`). Check container logs for
scheduling messages.

**Email not working** — CloudDump uses SMTP over SSL (port 465) by default.
Set `"smtp_security": "starttls"` for port 587, or `"smtp_security": "none"` for
plain SMTP relays. Verify the container can reach your SMTP server. Check logs
for `Failed to send email` messages.

**Run jobs now** — Send `SIGUSR1` to run all jobs immediately without waiting
for the cron schedule:

```sh
# Docker
docker kill -s USR1 clouddump

# Kubernetes
kubectl exec <pod> -- kill -USR1 1
```

**Debug mode** — Set `"debug": true` in your config for verbose logging.

## Monitoring

CloudDump exposes `GET /healthz` on port 8080 (configurable via `health_port`).

**Response:**

```json
{
  "status": "ok",
  "last_run": {
    "started": "2026-03-29T03:00:00+00:00",
    "finished": "2026-03-29T03:05:31+00:00",
    "finished_epoch": 1743220731,
    "jobs": 3,
    "succeeded": 3,
    "failed": 0
  },
  "jobs": {
    "prod-pg": {
      "type": "pgsql",
      "status": "success",
      "elapsed_seconds": 134,
      "rx_bytes": 1024000,
      "tx_bytes": 512
    },
    "prod-s3": {
      "type": "s3bucket",
      "status": "success",
      "elapsed_seconds": 187,
      "rx_bytes": 52428800,
      "tx_bytes": 1024
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `last_run` | Most recent backup run summary (`null` before first run) |
| `last_run.finished_epoch` | Unix timestamp for staleness detection |
| `jobs` | Per-job metrics from the most recent execution |
| `jobs.*.elapsed_seconds` | Job duration |
| `jobs.*.rx_bytes` / `tx_bytes` | Network I/O (Linux only) |

**Prometheus staleness alert:**

```promql
time() - clouddump_last_run_finished > 48 * 3600
```

## Contributing

Contributions are welcome. Please open an issue first to discuss what you'd
like to change.

## License

[MIT](LICENSE) — Copyright (c) 2026 Ralf Bjarne Taraldset
