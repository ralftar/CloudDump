# Vendanor CloudDump 📥 [![Publish Status](https://github.com/vendanor/CloudDump/workflows/Publish/badge.svg)](https://github.com/vendanor/CloudDump/actions)

CloudDump is a fully dockerized tool that schedules and executes data dumps from Azure blob storages, S3 buckets (including MinIO), and PostgreSQL databases. Jobs are run sequentially according to cron schedules, with email reports generated for each job. SMB and SSH shares can be mounted and used as backup destinations.

While CloudDump can be a useful component of a disaster recovery or backup regime (e.g. from cloud to on premises), it should not be used as a standalone backup tool, as it offers limited or no backup history, retention policies, and archival features. The tool is designed to create a current-state backup, which can then be fed into other tools for fully featured file-level backups.

## Features

- **Sequential Job Execution**: Jobs run in sequence, not in parallel, ensuring predictable resource usage
- **Cron-based Scheduling**: Standard cron patterns for job scheduling (e.g., `*/5 * * * *` for every 5 minutes)
- **Catch-up Execution**: If a scheduled time is missed while jobs are running, the job will run when checked to catch up on missed schedules
- **Stdout Logging**: All logs go to stdout for proper container log management
- **Email Reports**: Email reports with temporary log files attached for each job execution
- **Mount Support**: Support for SSH (sshfs) and SMB (smbnetfs) mounts without requiring elevated privileges
- **OpenSUSE Leap**: Built on OpenSUSE Leap 15.6

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

CloudDump runs as a single-process Docker container with a main loop that:

1. Checks every minute for jobs that match their cron schedule
2. Executes matching jobs sequentially (one at a time)
3. Looks backward in time from the last run to determine if a job should have run (catch-up execution)
4. Logs all output to stdout for container log management
5. Creates temporary log files that are attached to email reports and then deleted

This architecture ensures predictable resource usage and simplifies deployment and monitoring in containerized environments.

## Troubleshooting

### Container Won't Start

**Check configuration file syntax:**
```bash
docker logs clouddump
```

Common issues:
- Invalid JSON in config.json (use a JSON validator)
- Missing required fields (HOST, SMTPSERVER, etc.)
- Duplicate job IDs
- Invalid cron patterns (must have exactly 5 fields)

**Validate job configurations:**
The container performs comprehensive validation at startup:
- Job IDs must be unique
- Job types must be 's3bucket', 'azstorage', or 'pgsql'
- Required tools (aws, azcopy, pg_dump) must be available for each job type
- Cron schedules must be valid 5-field patterns

### Mount Failures

**SSH mounts:**
- Verify SSH connectivity: `ssh user@host`
- Check private key format (should be valid RSA/ED25519 key)
- Ensure StrictHostKeyChecking is appropriate for your environment

**SMB mounts:**
- Verify path format: `//hostname/sharename`
- Check credentials (username/password)
- Ensure network connectivity to SMB server

### Jobs Not Running

**Check cron schedule:**
- Verify cron pattern syntax (minute hour day month day-of-week)
- Remember: jobs run sequentially, so long-running jobs may delay others
- Check logs for "catch-up execution" messages

**Disk space issues:**
- Container checks for minimum 100MB free space before operations
- Monitor disk usage: `docker exec clouddump df -h`

### Email Not Sending

**Verify SMTP configuration:**
- Check SMTP server, port, and credentials in config.json
- Test from container: `docker exec clouddump mutt -s "Test" email@example.com < /dev/null`
- Check postfix logs: `docker exec clouddump tail /var/log/postfix.log`

**Common SMTP issues:**
- Port 465 requires TLS/SSL
- Some providers require app-specific passwords
- Check firewall rules for outbound SMTP

### Performance Issues

**Large data transfers:**
- Jobs run sequentially to avoid resource contention
- Consider scheduling large jobs during off-peak hours
- Monitor with: `docker stats clouddump`

**Slow PostgreSQL dumps:**
- Consider excluding large tables with `tables_excluded`
- Use compression (`compress: true`) to reduce disk I/O
- Verify network bandwidth to database server

### Debugging

**Enable debug mode:**
```json
{
  "settings": {
    "DEBUG": true
  },
  "jobs": [
    {
      "debug": true
    }
  ]
}
```

**View detailed logs:**
```bash
# Follow logs in real-time
docker logs -f clouddump

# View last 100 lines
docker logs --tail 100 clouddump

# Check for errors only
docker logs clouddump 2>&1 | grep ERROR
```

## License

This tool is released under the MIT License.
