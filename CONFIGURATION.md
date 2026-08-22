# Configuration reference

CloudDump is configured via a single JSON file mounted at `/config/config.json`.

## Execution model

All jobs share a single top-level `crontab`. When the schedule triggers,
every job runs in sequence — in the order listed in the config file.
No jobs are skipped.

This is intentional. Sequential execution prevents resource contention
(disk I/O, network bandwidth) and keeps behavior predictable. If you need
parallel execution or isolated scheduling, run multiple CloudDump instances
with separate configurations and backup destinations.

## Top-level settings

All settings are top-level keys in `config.json`, alongside `jobs`.

| Key | Required | Description |
|-----|----------|-------------|
| `host` | **Yes** | Instance name shown in emails and logs (e.g. `"prod-backup"`, `"dr-site"`) |
| `debug` | No | Stream tool output and debug messages to console (`true`/`false`, default `false`) |
| `log_format` | No | Log output format: `"text"` (default, human-readable) or `"json"` (structured) |
| `smtp_server` | No | SMTP server |
| `smtp_port` | No | SMTP port (465 for SSL, 25/587 for plain) |
| `smtp_user` | No | SMTP username |
| `smtp_pass` | No | SMTP password |
| `smtp_security` | No | Encryption mode: `"ssl"` (default, port 465), `"starttls"` (port 587), `"none"` (plain) |
| `mail_from` | No | Sender address (e.g. `"backup@example.com"` or `"CloudDump <backup@example.com>"`) |
| `mail_to` | No | Recipient address(es) — comma-separated or JSON array |
| `email_log_attached` | No | Attach full log file to job report emails (`true`/`false`, default `false`) |
| `email_size_limit_mb` | No | Max email size (MB, default `15`). Over this, attachments are gzipped individually; if still over, they are dropped and an error is logged. The report body always sends. |
| `crontab` | **Yes** | Standard 5-field cron expression — schedule for running all jobs |
| `health_port` | No | Port for the HTTP health endpoint (`1`–`65535`, default `8080`) |
| `health_log` | No | Log health-check HTTP requests at DEBUG level (`true`/`false`, default `false`) |



Email is optional. If SMTP is not configured, CloudDump runs silently.

`mail_from` is used directly as the `From` header. Use a bare address
(`"backup@example.com"`) or include a display name (`"CloudDump <backup@example.com>"`).
Avoid putting the email address as the display name (e.g.
`"backup@example.com <backup@example.com>"`) — mail clients like Outlook flag this
pattern as a phishing indicator.

`mail_to` accepts multiple recipients as a comma-separated string
(`"ops@example.com, oncall@example.com"`) or a JSON array
(`["ops@example.com", "oncall@example.com"]`).

## Signals

| Signal | Effect |
|--------|--------|
| `SIGTERM` / `SIGINT` | Graceful shutdown — forwards to running child process |
| `SIGUSR1` | Run all jobs immediately (skip cron schedule) |

```sh
# Docker
docker kill -s USR1 clouddump

# Kubernetes
kubectl exec deploy/clouddump -- kill -USR1 1
```

## Job fields

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `id` | Yes | — | Unique job identifier |
| `type` | Yes | — | `s3bucket`, `azstorage`, `pgsql`, `mysql`, `github`, `rsync`, or `imap` |
| `enabled` | No | `true` | Skip the job when `false`. Still validated at startup. |
| `timeout` | No | `604800` (7 days) | Job timeout in seconds |
| `retries` | No | `3` | Number of attempts on failure |

Plus type-specific fields (`buckets`, `blobstorages`, `servers`, `organizations`,
`targets`, `accounts`) — see below.

## S3 bucket

```json
{
  "type": "s3bucket",
  "id": "my-s3-job",

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

## Azure Blob Storage

```json
{
  "type": "azstorage",
  "id": "my-azure-job",

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

## PostgreSQL

```json
{
  "type": "pgsql",
  "id": "my-pg-job",

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
- `db_retries`: number of retry attempts per individual database dump (default: `3`).

## MySQL / MariaDB

```json
{
  "type": "mysql",
  "id": "my-mysql-job",

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
- `db_retries`: number of retry attempts per individual database dump (default: `3`).

Dumps use `--single-transaction --routines --triggers --events` for
consistent, complete backups without locking tables.

## GitHub organization or user

```json
{
  "type": "github",
  "id": "my-github-job",

  "organizations": [
    {
      "name": "my-org",
      "account_type": "org",
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

- `account_type`: `"org"` (default) for organizations, or `"user"` for personal accounts. At startup CloudDump verifies the token and account via the GitHub API.
- `token`: GitHub personal access token with `repo` and `read:org` scopes.
- `repositories`: list of specific repository names to back up (default: all repositories).
- `include_repos`: mirror-clone repositories with all branches, tags, and PR refs (default: `true`).
- `include_issues`: back up issues, comments, and events (default: `false`).
- `include_pulls`: back up pull requests, comments, commits, and details (default: `false`).
- `include_labels`: back up labels (default: `false`).
- `include_milestones`: back up milestones (default: `false`).
- `include_releases`: back up releases and assets (default: `false`).
- `include_wikis`: back up repository wikis (default: `false`).
- `include_forks`: include forked repositories (default: `false`).
- `include_archived`: include archived repositories (default: `true`).
- `include_lfs`: download Git LFS objects (default: `false`).

By default only repository code is backed up. Metadata options (issues, pulls, labels, milestones, releases, wikis) can be enabled individually but require many GitHub API calls per repository.

## Rsync over SSH

```json
{
  "type": "rsync",
  "id": "my-rsync-job",

  "targets": [
    {
      "source": "user@server.example.com:/data/important/",
      "destination": "/mnt/clouddump/rsync",
      "ssh_key": "/config/id_ed25519",
      "ssh_port": 22,
      "delete_destination": true,
      "delete_excluded": false,
      "exclude": ["*.tmp", "cache/"],
      "min_age_days": 30
    }
  ]
}
```

- `source`: remote path in `user@host:/path` format (required).
- `destination`: local backup directory (required).
- `ssh_key`: path to the SSH private key file, mounted into the container (required).
- `ssh_port`: SSH port (default: `22`).
- `delete_destination`: remove files at destination that no longer exist at source (default: `true`). When combined with `min_age_days`, the destination becomes an exact mirror of the filtered file set: any destination file that is **not** in the age-filtered list is removed. This means files newer than `min_age_days` will **not** be present at the destination. Set `delete_destination` to `false` if you want to accumulate old files while keeping previously synced files intact.
- `exclude`: list of rsync exclude patterns (default: none).
- `delete_excluded`: also delete `exclude`d paths from the destination (default: `false`). Implies deletion (`--delete`), so already-mirrored copies of newly-excluded paths (e.g. regenerable caches) are purged on the next run. Without this, excluded paths already present at the destination are left untouched.
- `min_age_days`: only copy files whose modification time is older than this many days (default: none — copy all files). When set, CloudDump enumerates remote files via `rsync --list-only`, filters by mtime client-side, and passes the qualifying paths to the main rsync with `--files-from`. Uses the rsync protocol only — no remote shell commands — so it works with restricted SSH accounts (forced commands, `rrsync`, etc.).

The SSH key file should be mounted read-only into the container:

```sh
docker run -d \
  -v /path/to/id_ed25519:/config/id_ed25519:ro \
  -v /mnt/nas/clouddump:/backup \
  -v $(pwd)/config.json:/config/config.json:ro \
  ghcr.io/ralftar/clouddump:latest
```

SSH uses `StrictHostKeyChecking=accept-new` (auto-accepts new host keys but
rejects changed ones) and `BatchMode=yes` (never prompts for passwords).

## IMAP mailbox

Mirrors an IMAP account to a local **Maildir** — one directory per folder,
one file per message (raw RFC822). Uses [`mbsync`](https://isync.sourceforge.io/)
(isync). The sync is strictly one-directional: **the remote mailbox is never
modified**. Works against any IMAP server; the primary use case is Proton Mail
via Proton Bridge.

```json
{
  "type": "imap",
  "id": "my-mail-job",

  "accounts": [
    {
      "host": "127.0.0.1",
      "port": 1143,
      "user": "you@proton.me",
      "pass": "bridge-generated-password",
      "destination": "/mnt/clouddump/proton",
      "tls": "starttls",
      "cert_file": "/config/bridge-cert.pem",
      "delete_destination": true,
      "exclude": ["All Mail"]
    }
  ]
}
```

- `host`: IMAP server hostname or IPv4 (required). For Proton Bridge running as
  a sidecar, this is `127.0.0.1` or the compose service name.
- `port`: IMAP port. Defaults to `993` when `tls` is `"ssl"`, otherwise `143`.
  Proton Bridge listens on `1143`, so set it explicitly.
- `user`: IMAP username — usually the full email address (required).
- `pass`: IMAP password (required). For Proton this is the **Bridge-generated**
  password, not your Proton account password.
- `destination`: local Maildir root directory (required). mbsync keeps its sync
  state here, so the directory is self-contained across runs.
- `tls`: transport security — `"ssl"` (default, IMAPS), `"starttls"`, or
  `"none"`. Use `"starttls"` for Proton Bridge on port 1143.
- `cert_file`: path to a CA/cert to trust, for self-signed servers like Proton
  Bridge. Export it from Bridge's settings and mount it into the container.
- `delete_destination`: mirror mode — propagate remote deletions to the local
  copy (default: `true`). The remote mailbox is never touched. Set to `false`
  to keep messages locally after they are deleted on the server.
- `exclude`: IMAP folder names to skip (default: none), e.g. `"All Mail"` to
  avoid duplicating every message that also lives in a label.

### Proton Mail via Proton Bridge

Proton Bridge is **not** part of the CloudDump image — it runs as its own
container (a sidecar), so it is updated on its own release cadence and its
decrypted-mail/session state stays isolated. CloudDump just points the generic
`imap` job at the Bridge's local IMAP port.

```yaml
services:
  protonmail-bridge:
    image: shenxn/protonmail-bridge:latest
    restart: unless-stopped
    volumes:
      - bridge-data:/root  # persists keychain + session across restarts

  clouddump:
    image: ghcr.io/ralftar/clouddump:latest
    restart: unless-stopped
    depends_on: [protonmail-bridge]
    volumes:
      - ./config.json:/config/config.json:ro
      - /mnt/nas/clouddump:/backup

volumes:
  bridge-data:
```

With this compose, set the account's `host` to `protonmail-bridge` (the service
name) and `port` to `1143`.

**One-time setup:** Bridge requires a single interactive login (username +
password + 2FA) that cannot be automated. Run it once:

```sh
docker exec -it protonmail-bridge protonmail-bridge --cli
# then: login   (enter credentials + 2FA), and note the Bridge IMAP password
```

After that the session is cached in the `bridge-data` volume and survives
restarts — all subsequent syncs run unattended. Proton Bridge requires a paid
Proton plan.

## Storage

CloudDump writes backups to local paths (`destination` / `backuppath`).
It does not manage remote mounts — that is the host's or orchestrator's
responsibility. Use Docker bind mounts, Kubernetes PersistentVolumes,
or any storage backend your runtime supports.

Example with a Docker bind mount to a host-mounted SMB share:

```sh
docker run -d \
  -v /mnt/nas/clouddump:/backup \
  -v $(pwd)/config.json:/config/config.json:ro \
  ghcr.io/ralftar/clouddump:latest
```

Example with a Kubernetes SMB PVC:

```yaml
volumes:
  - name: backup
    persistentVolumeClaim:
      claimName: smb-clouddump
containers:
  - name: clouddump
    volumeMounts:
      - name: backup
        mountPath: /backup
```
