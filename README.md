# Vendanor CloudDump [![Publish Status](https://github.com/vendanor/CloudDump/workflows/Publish/badge.svg)](https://github.com/vendanor/CloudDump/actions)

CloudDump is a fully dockerized tool that schedules and executes data dumps from Azure blob storages and PostgreSQL databases. Jobs are run sequentially according to cron schedules, with email reports generated for each job. SMB and SSH shares can be mounted and used as backup destinations.

While CloudDump can be a useful component of a disaster recovery or backup regime (e.g. from cloud to on premises), it should not be used as a standalone backup tool, as it offers limited or no backup history, retention policies, and archival features. The tool is designed to create a current-state backup, which can then be fed into other tools for fully featured file-level backups.

## Features

- **Sequential Job Execution**: Jobs run in sequence, not in parallel, ensuring predictable resource usage
- **Cron-based Scheduling**: Standard cron patterns for job scheduling (e.g., `*/5 * * * *` for every 5 minutes)
- **Skip Missed Schedules**: If a scheduled time is missed while jobs are running, it will be skipped to avoid backlog
- **Stdout Logging**: All logs go to stdout for proper container log management
- **Email Reports**: Email reports with temporary log files attached for each job execution
- **Mount Support**: Support for SSH (sshfs) and SMB (CIFS) mounts
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

To give mount permissions, add capabilities DAC_READ_SEARCH and SYS_ADMIN. Example.:

```docker run --name "clouddump" --cap-add DAC_READ_SEARCH --cap-add SYS_ADMIN --mount type=bind,source=config.json,target=/config/config.json,readonly --volume /clouddump/:/mnt/clouddump -d --restart always ghcr.io/vendanor/clouddump:latest```


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
          "script": "azdump.sh",
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
          "script": "pgdump.sh",
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
              "databases_excluded": [
                "azure_sys",
                "azure_maintenance",
                "template0"
              ],
              "backuppath": "/pgdump",
              "filenamedate": true,
              "compress": true
            }
          ]
        }
      ]
    }
       
## Configuration

### PostgreSQL Backup Configuration

The PostgreSQL backup configuration (`pgdump.sh`) supports flexible database and table filtering:

#### Database Selection

- **Specific databases**: List databases in the `databases` array. Only these databases will be backed up.
  ```json
  "databases": [
    {
      "mydb": {
        "tables_included": [],
        "tables_excluded": ["table1", "table2"]
      }
    },
    {
      "anotherdb": {
        "tables_included": ["important_table"],
        "tables_excluded": []
      }
    }
  ]
  ```

- **All databases with exclusions**: If `databases` array is empty, all databases will be backed up except those in `databases_excluded`:
  ```json
  "databases": [],
  "databases_excluded": ["azure_sys", "azure_maintenance", "template0"]
  ```

#### Table Filtering (per database)

For each database, you can optionally specify:
- `tables_included`: If specified, only these tables will be backed up (mutually exclusive with `tables_excluded`)
- `tables_excluded`: Tables to exclude from the backup

**Note**: `tables_included` and `tables_excluded` should not be used together. If `tables_included` is specified, only those tables will be backed up. If `tables_excluded` is specified, all tables except those will be backed up.

## Architecture

CloudDump runs as a single-process Docker container with a main loop that:

1. Checks every minute for jobs that match their cron schedule
2. Executes matching jobs sequentially (one at a time)
3. Skips schedules that were missed while jobs were running
4. Logs all output to stdout for container log management
5. Creates temporary log files that are attached to email reports and then deleted

This architecture ensures predictable resource usage and simplifies deployment and monitoring in containerized environments.

## License

This tool is released under the MIT License.
