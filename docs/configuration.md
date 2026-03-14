# Configuration reference

CloudDump is configured via a single JSON file mounted at `/config/config.json`.

## Settings

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

## Job fields

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `id` | Yes | — | Unique job identifier |
| `type` | Yes | — | `s3bucket`, `azstorage`, `pgsql`, `mysql`, or `github` |
| `crontab` | Yes | — | 5-field cron pattern |
| `timeout` | No | `604800` (7 days) | Job timeout in seconds |
| `retries` | No | `3` | Number of attempts on failure |

Plus type-specific fields (`buckets`, `blobstorages`, `servers`, `organizations`)
— see below.

## S3 bucket

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

## Azure Blob Storage

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

## PostgreSQL

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

## MySQL / MariaDB

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

## GitHub organization

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

## Mounts

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
