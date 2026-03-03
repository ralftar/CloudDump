# Vendanor CloudDump :inbox_tray: [![Publish Status](https://github.com/vendanor/CloudDump/workflows/Publish/badge.svg)](https://github.com/vendanor/CloudDump/actions)

CloudDump is a fully dockerized tool that schedules and executes data dumps from Azure blob storages, S3 buckets (including MinIO), and PostgreSQL databases. Jobs are run sequentially according to cron schedules, with email reports generated for each job. SMB and SSH shares can be mounted and used as backup destinations.

While CloudDump can be a useful component of a disaster recovery or backup regime (e.g. from cloud to on premises), it should not be used as a standalone backup tool, as it offers limited or no backup history, retention policies, and archival features. The tool is designed to create a current-state backup, which can then be fed into other tools for fully featured file-level backups.

## Features

- **Sequential Job Execution**: Jobs run in sequence, not in parallel, ensuring predictable resource usage
- **Cron-based Scheduling**: Standard cron patterns for job scheduling (e.g., `*/5 * * * *` for every 5 minutes)
- **Catch-up Execution**: If a scheduled time is missed while jobs are running, the job will run when checked to catch up on missed schedules
- **Stdout Logging**: All logs go to stdout for proper container log management
- **Email Reports**: Email reports with temporary log files attached for each job execution
- **Mount Support**: Support for SSH (sshfs) and SMB (smbnetfs) mounts without requiring elevated privileges
- **Debian**: Built on Debian 12 (Bookworm)

## Running

```docker
docker run \
  --name "clouddump"  \
  --mount type=bind,source=config.json,target=/config/config.json,readonly \
  --volume /clouddump/:/mnt/clouddump \
  -d --restart always \
  ghcr.io/vendanor/clouddump:latest
```


### config.json example

    {
      "settings": {
        "HOST": "host.domain.dom",
        "SMTPSERVER": "smtp.domain.dom",
        "SMTPPORT": "465",
        "SMTPUSER": "username",
        "SMTPPASS": "password",
        "MAILFROM": "user@domain.dom",
        "MAILTO": "user@domain.dom",
        "DEBUG": false,
        "mount": [
          {
            "path": "host:/share",
            "mountpoint": "/mnt/smb",
            "username": "user",
            "password": "pass",
            "privkey": ""
          }
        ]
      },
      "jobs": [
        {
          "type": "azstorage",
          "id": "azdump1",
          "crontab": "*/5 * * * *",
          "debug": false,
          "blobstorages": [
            {
              "source": "https://example.blob.core.windows.net/test?etc",
              "destination": "/azdump/azdump1",
              "delete_destination": true
            }
          ]
        },
        {
          "type": "s3bucket",
          "id": "s3dump1",
          "crontab": "0 2 * * *",
          "debug": false,
          "buckets": [
            {
              "source": "s3://my-bucket/path",
              "destination": "/s3dump/s3dump1",
              "delete_destination": false,
              "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
              "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
              "aws_region": "us-east-1",
              "endpoint_url": ""
            }
          ]
        },
        {
          "type": "pgsql",
          "id": "pgdump1",
          "crontab": "* * * * *",
          "debug": false,
          "servers": [
            {
              "host": "example.azure.com",
              "port": 5432,
              "user": "username",
              "pass": "password",
              "databases": [
                {
                  "mydb": {
                    "tables_included": [],
                    "tables_excluded": [
                      "table1",
                      "table2"
                    ]
                  }
                }
              ],
              "databases_excluded": [],
              "backuppath": "/pgdump",
              "filenamedate": true,
              "compress": true
            }
          ]
        }
      ]
    }

### MinIO Configuration Example

For MinIO or other S3-compatible storage, use the `endpoint_url` parameter:

    {
      "type": "s3bucket",
      "id": "miniodump1",
      "crontab": "0 3 * * *",
      "debug": false,
      "buckets": [
        {
          "source": "s3://my-bucket/path",
          "destination": "/s3dump/miniodump1",
          "delete_destination": false,
          "aws_access_key_id": "minioadmin",
          "aws_secret_access_key": "minioadmin",
          "aws_region": "us-east-1",
          "endpoint_url": "https://minio.example.com:9000"
        }
      ]
    }

## Architecture

CloudDump runs as a single-process Docker container with a Python orchestrator (`start.py`) that:

1. Checks every minute for jobs that match their cron schedule
2. Executes matching jobs sequentially (one at a time)
3. Looks backward in time from the last run to determine if a job should have run (catch-up execution)
4. Logs all output to stdout for container log management
5. Creates temporary log files that are attached to email reports and then deleted
6. Sends email reports directly via `smtplib` (SMTP over SSL)
7. Forwards SIGTERM to running child processes for graceful shutdown

External tools invoked by subprocess: `aws`, `azcopy`, `pg_dump`, `psql`, `sshfs`, `smbnetfs`, `bzip2`.

This architecture ensures predictable resource usage and simplifies deployment and monitoring in containerized environments.

## Troubleshooting

### Container Won't Start

- **Missing config file**: Ensure `config.json` is mounted at `/config/config.json`. Check that the mount path is correct and the file is readable.
- **Invalid JSON**: Validate your `config.json` with `python3 -m json.tool config.json` before mounting.
- **Missing required tools**: The startup script validates that required tools (`aws`, `azcopy`, `pg_dump`, `psql`) are available for each configured job type. Check container logs for errors.

### Mount Failures

- **SSH mounts**: Ensure the SSH key is valid and the remote host is reachable. Check that the path format is `user@host:/path` or `host:/path` (with username configured separately).
- **SMB mounts**: Ensure the path format is `//host/share`. Verify credentials are correct. The container uses `smbnetfs` which requires FUSE support — ensure the container has the necessary privileges.
- **Permission denied**: Verify the mount credentials and that the remote share allows access from the container's network.

### Jobs Not Running

- **Check cron syntax**: CloudDump supports `*`, exact values (e.g., `5`), and step values (e.g., `*/15`). Ranges (`1-5`) and lists (`1,3,5`) are not supported.
- **Catch-up execution**: If a job was scheduled while another was running, it will catch up on the next check cycle. Jobs run sequentially, not in parallel.
- **Missing tools**: S3 jobs require `aws`, Azure Storage jobs require `azcopy`, and PostgreSQL jobs require `pg_dump` and `psql`. Check logs for tool-related errors.

### Email Not Sending

- **SMTP configuration**: Verify `SMTPSERVER`, `SMTPPORT`, `SMTPUSER`, and `SMTPPASS` in your config. CloudDump uses SMTPS (port 465) via Python's `smtplib`.
- **Firewall**: Ensure the container can reach the SMTP server on the configured port.

### Performance Issues

- **Long sync times**: Check the email reports for job duration. For S3, consider using `endpoint_url` for closer S3-compatible endpoints. For Azure, ensure the container is in the same region as the storage account.
- **Disk space**: Ensure the backup destination has sufficient space. Failed syncs may leave partial data.

### Debugging

Enable debug mode for verbose logging:

```json
{
  "settings": {
    "DEBUG": true
  }
}
```

Setting `DEBUG` to `true` enables debug-level log output from the Python orchestrator.

## License

This tool is released under the MIT License.
