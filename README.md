# Vendanor CloudDump 📥 [![Publish Status](https://github.com/vendanor/CloudDump/workflows/Publish/badge.svg)](https://github.com/vendanor/CloudDump/actions)

CloudDump is a fully dockerized tool that schedules and executes data dumps from Azure blob storages, S3 buckets (including MinIO), and PostgreSQL databases. Jobs are run sequentially according to cron schedules, with email reports generated for each job. SMB and SSH shares can be mounted and used as backup destinations.

While CloudDump can be a useful component of a disaster recovery or backup regime (e.g. from cloud to on premises), it should not be used as a standalone backup tool, as it offers limited or no backup history, retention policies, and archival features. The tool is designed to create a current-state backup, which can then be fed into other tools for fully featured file-level backups.

## Features

- **Sequential Job Execution**: Jobs run in sequence, not in parallel, ensuring predictable resource usage
- **Cron-based Scheduling**: Standard cron patterns for job scheduling (e.g., `*/5 * * * *` for every 5 minutes)
- **Skip Missed Schedules**: If a scheduled time is missed while jobs are running, it will be skipped to avoid backlog
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
3. Skips schedules that were missed while jobs were running
4. Logs all output to stdout for container log management
5. Creates temporary log files that are attached to email reports and then deleted

This architecture ensures predictable resource usage and simplifies deployment and monitoring in containerized environments.

### How Cron Scheduling Works

CloudDump implements a custom cron-like scheduler that determines when to run or skip jobs. Here's how it works:

**Time Checking and Matching**
- The main loop wakes up approximately every minute by sleeping until the next minute boundary (calculated as `60 - current_seconds`)
- On each iteration, it evaluates the current time (minute, hour, day, month, day-of-week) against each job's cron pattern
- The `check_cron_match()` function supports standard cron syntax including:
  - Wildcards: `*` (matches any value)
  - Step values: `*/5` (every 5 units)
  - Ranges: `1-5` (values 1 through 5)
  - Lists: `1,3,5` (specific values)
  - Exact matches: `15` (only when value equals 15)

**Deduplication Mechanism**
- Each job has a `last_run_times` timestamp tracking when it last executed
- When a cron pattern matches, the script checks: "Did this job already run in the current minute?"
- This prevents duplicate execution even if the loop wakes up multiple times within the same minute
- The comparison uses the minute precision: `YYYY-MM-DD HH:MM`

**Handling Missed Schedules**
- If a job is running when its next scheduled time arrives, that schedule is skipped
- Example: A job scheduled for `*/5 * * * *` (every 5 minutes) that starts at 10:00 and runs until 10:07 will miss the 10:05 schedule
- The job will run again at 10:10 (the next matching schedule after it finishes)
- This "skip missed schedules" behavior prevents job backlog and ensures predictable resource usage

**Why It Doesn't Need Exact Timing**
- The loop doesn't need to wake up at exactly :00 seconds of each minute
- When it wakes up at any point (e.g., :03 seconds), it checks if the current minute matches any cron patterns
- If a pattern matches and the job hasn't run yet in that minute, it will execute
- The minute-boundary sleep is just an optimization to avoid unnecessary wake-ups

**Example Timeline**
```
10:00:03 - Loop wakes up, checks jobs, finds job1 matches "*/5 * * * *", runs job1
10:00:45 - Job1 completes, last_run_times[job1] = timestamp for 10:00
10:01:02 - Loop wakes up, checks jobs, job1 doesn't match (not a */5 minute)
10:05:01 - Loop wakes up, checks jobs, job1 matches and last ran at 10:00, runs job1
10:05:30 - Job1 still running...
10:06:01 - Loop wakes up, checks jobs, job1 already ran in minute 10:05, skips
```

This design ensures reliable scheduling without depending on precise timing, while preventing duplicate runs and managing resource usage effectively.

## License

This tool is released under the MIT License.
