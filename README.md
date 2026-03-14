# CloudDump 🌩️

[![CI](https://github.com/ralftar/CloudDump/actions/workflows/ci.yml/badge.svg)](https://github.com/ralftar/CloudDump/actions/workflows/ci.yml)
[![Publish](https://github.com/ralftar/CloudDump/actions/workflows/publish.yml/badge.svg)](https://github.com/ralftar/CloudDump/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Keep a copy of your cloud data somewhere you control.**

The cloud is just someone else's computer. CloudDump pulls your persistent
data — S3 buckets, Azure Blob Storage, PostgreSQL databases, MySQL
databases, GitHub organizations — down to
on-premises storage, another cloud, or wherever you want. On a schedule,
unattended, with email notifications when things succeed or fail.

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

## Quick start

**1. Create a config file**

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

## Configuration reference

### Settings

| Key | Required | Description |
|-----|----------|-------------|
| `HOST` | No | Hostname shown in email subjects |
| `SMTPSERVER` | No | SMTP server (SSL, port 465) |
| `SMTPPORT` | No | SMTP port |
| `SMTPUSER` | No | SMTP username |
| `SMTPPASS` | No | SMTP password |
| `MAILFROM` | No | Sender address |
| `MAILTO` | No | Recipient address(es) — comma-separated or JSON array |
| `DEBUG` | No | Enable debug logging (`true`/`false`) |
| `mount` | No | Array of SSH/SMB mount definitions |

Email is optional. If SMTP is not configured, CloudDump runs silently.
`MAILTO` accepts multiple recipients as a comma-separated string
(`"ops@example.com, oncall@example.com"`) or a JSON array
(`["ops@example.com", "oncall@example.com"]`).

### Job fields

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `id` | Yes | — | Unique job identifier |
| `type` | Yes | — | `s3bucket`, `azstorage`, `pgsql`, `mysql`, or `github` |
| `crontab` | Yes | — | 5-field cron pattern |
| `timeout` | No | `604800` (7 days) | Job timeout in seconds |
| `retries` | No | `3` | Number of attempts on failure |

Plus type-specific fields (`buckets`, `blobstorages`, `servers`, `organizations`)
— see examples below.

### S3 bucket

```json
{
  "type": "s3bucket",
  "id": "my-s3-job",
  "crontab": "0 2 * * *",
  "buckets": [
    {
      "source": "s3://bucket-name/optional-prefix",
      "destination": "/mnt/clouddump/s3",
      "delete_destination": false,
      "aws_access_key_id": "AKIA...",
      "aws_secret_access_key": "...",
      "aws_region": "us-east-1",
      "endpoint_url": ""
    }
  ]
}
```

Set `endpoint_url` for S3-compatible storage like MinIO:

```json
"endpoint_url": "https://minio.example.com:9000"
```

### Azure Blob Storage

```json
{
  "type": "azstorage",
  "id": "my-azure-job",
  "crontab": "*/5 * * * *",
  "blobstorages": [
    {
      "source": "https://account.blob.core.windows.net/container?sv=...&sig=...",
      "destination": "/mnt/clouddump/azure",
      "delete_destination": true
    }
  ]
}
```

The source URL includes the SAS token for authentication.

### PostgreSQL

```json
{
  "type": "pgsql",
  "id": "my-pg-job",
  "crontab": "0 4 * * *",
  "servers": [
    {
      "host": "db.example.com",
      "port": 5432,
      "user": "backup_user",
      "pass": "password",
      "databases": [
        { "mydb": { "tables_included": [], "tables_excluded": ["large_logs"] } }
      ],
      "databases_excluded": ["template0", "template1"],
      "backuppath": "/mnt/clouddump/pg",
      "filenamedate": true,
      "compress": true
    }
  ]
}
```

- `databases`: explicit list with per-database table filters. If empty, all
  databases are dumped (except `databases_excluded`).
- `compress`: bzip2 compression of dump files.
- `filenamedate`: append timestamp to dump filenames.

### MySQL / MariaDB

```json
{
  "type": "mysql",
  "id": "my-mysql-job",
  "crontab": "0 4 * * *",
  "servers": [
    {
      "host": "mysql.example.com",
      "port": 3306,
      "user": "backup_user",
      "pass": "password",
      "databases": ["app_db", "analytics"],
      "databases_excluded": [],
      "backuppath": "/mnt/clouddump/mysql",
      "filenamedate": true,
      "compress": true
    }
  ]
}
```

- `databases`: explicit list. If empty, all databases are dumped (except
  `databases_excluded` and system databases `information_schema`,
  `performance_schema`, `sys`).
- `compress`: bzip2 compression of dump files.
- `filenamedate`: append timestamp to dump filenames.

Dumps use `--single-transaction --routines --triggers --events` for
consistent, complete backups without locking tables.

### GitHub organization

```json
{
  "type": "github",
  "id": "my-github-job",
  "crontab": "0 5 * * *",
  "organizations": [
    {
      "name": "my-org",
      "destination": "/mnt/clouddump/github",
      "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "include_repos": true,
      "include_issues": true,
      "include_pulls": true,
      "include_labels": true,
      "include_milestones": true,
      "include_releases": true,
      "include_wikis": true,
      "include_forks": false,
      "include_archived": true,
      "include_lfs": false
    }
  ]
}
```

- `token`: GitHub personal access token with `repo` and `read:org` scopes.
- `include_repos`: mirror-clone repositories with all branches, tags, and PR refs (default: `true`).
- `include_issues`: back up issues, comments, and events (default: `true`).
- `include_pulls`: back up pull requests, comments, commits, and details (default: `true`).
- `include_labels`: back up labels (default: `true`).
- `include_milestones`: back up milestones (default: `true`).
- `include_releases`: back up releases and assets (default: `true`).
- `include_wikis`: back up repository wikis (default: `true`).
- `include_forks`: include forked repositories (default: `false`).
- `include_archived`: include archived repositories (default: `true`).
- `include_lfs`: download Git LFS objects (default: `false`).

All `include_*` options default to `true` except `include_forks` and `include_lfs`.

### Mounts

```json
"mount": [
  {
    "path": "user@host:/remote/path",
    "mountpoint": "/mnt/ssh-target",
    "privkey": "/config/id_rsa"
  },
  {
    "path": "//server/share",
    "mountpoint": "/mnt/smb-target",
    "username": "user",
    "password": "pass"
  }
]
```

Mounts are set up at startup before any jobs run. Use them as backup
destinations in your job configs.

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
