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
| `mail_from` | No | Sender address |
| `mail_to` | No | Recipient address(es) — comma-separated or JSON array |
| `email_log_attached` | No | Attach full log file to job report emails (`true`/`false`, default `false`) |
| `crontab` | **Yes** | Standard 5-field cron expression — schedule for running all jobs |
| `health_port` | No | Port for the HTTP health endpoint (`1`–`65535`, default `8080`) |
| `health_log` | No | Log health-check HTTP requests at DEBUG level (`true`/`false`, default `false`) |



Email is optional. If SMTP is not configured, CloudDump runs silently.
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
| `type` | Yes | — | `s3bucket`, `azstorage`, `pgsql`, `mysql`, `github`, or `rsync` |
| `timeout` | No | `604800` (7 days) | Job timeout in seconds |
| `retries` | No | `3` | Number of attempts on failure |

Plus type-specific fields (`buckets`, `blobstorages`, `servers`, `organizations`,
`targets`) — see below.

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
- `min_age_days`: only copy files whose modification time is older than this many days (default: none — copy all files). When set, CloudDump SSHs to the remote to discover qualifying files via `find -mtime`, then passes the list to rsync with `--files-from`. Requires GNU `find` on the remote; falls back to POSIX `find` + `sed` on BSD/macOS.

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
