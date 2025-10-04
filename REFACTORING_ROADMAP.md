# Refactoring Roadmap

This document outlines a phased approach to refactoring CloudDump for better maintainability, testability, and extensibility.

## Overview

The goal is to transform start.sh from a monolithic 1030-line script into a modular, maintainable codebase while preserving all existing functionality.

## Current State

```
CloudDump/
├── start.sh (1030 lines) - Everything
├── dump_s3bucket.sh (218 lines)
├── dump_azstorage.sh (146 lines)
├── dump_pgsql.sh (387 lines)
└── install_*.sh
```

**Problems:**
- All logic in one file
- No separation of concerns
- Difficult to test
- Hard to extend
- Code duplication

## Target State

```
CloudDump/
├── start.sh (100 lines) - Main entry point
├── lib/
│   ├── config.sh - Configuration management
│   ├── logging.sh - Logging utilities
│   ├── scheduling.sh - Cron pattern matching
│   ├── email.sh - Email reporting
│   ├── jobs.sh - Job execution orchestration
│   └── mounts.sh - Mount management
├── dump/
│   ├── s3bucket.sh
│   ├── azstorage.sh
│   └── pgsql.sh
├── tests/
│   ├── test_config.sh
│   ├── test_scheduling.sh
│   ├── test_jobs.sh
│   └── fixtures/
└── docs/
```

---

## Phase 1: Foundation (Week 1)

### Goals
- Fix critical issues
- Establish testing infrastructure
- Create library structure

### Tasks

#### 1.1 Fix Critical Issues
- [ ] Apply fixes from CRITICAL_FIXES.md
- [ ] Run shellcheck and fix all warnings
- [ ] Add configuration validation
- [ ] Fix security issues

#### 1.2 Create Library Structure
```bash
mkdir -p lib tests tests/fixtures docs
```

#### 1.3 Extract Common Utilities

**Create: lib/common.sh**
```bash
#!/bin/bash

# Common utility functions used across the codebase

# Timestamp for logging
timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

# Log message to stdout
log() {
  echo "[$(timestamp)] $*"
}

# Log error to stderr
error() {
  echo "[$(timestamp)] ERROR: $*" >&2
}

# Redact sensitive information from text
redact_sensitive() {
  local text="$1"
  # Redact passwords, keys, tokens, and SAS tokens
  text="${text//password: */password: [REDACTED]}"
  text="${text//pass: */pass: [REDACTED]}"
  text="${text//key: */key: [REDACTED]}"
  text="${text//token: */token: [REDACTED]}"
  text="${text//secret: */secret: [REDACTED]}"
  # Redact SAS query strings
  text=$(echo "${text}" | sed 's/\?[^?]*\(sig\|se\|st\|sp\)=[^&?]*/\?[REDACTED]/g')
  echo "${text}"
}

# Check if command exists
command_exists() {
  command -v "$1" >/dev/null 2>&1
}

# Validate required commands
check_required_commands() {
  local cmds="$*"
  local missing=""
  
  for cmd in ${cmds}; do
    if ! command_exists "${cmd}"; then
      if [ -z "${missing}" ]; then
        missing="${cmd}"
      else
        missing="${missing} ${cmd}"
      fi
    fi
  done
  
  if [ -n "${missing}" ]; then
    error "Missing required commands: ${missing}"
    return 1
  fi
  
  return 0
}
```

**Update start.sh:**
```bash
# Near the top (after line 16)
source /usr/local/bin/lib/common.sh || exit 1
```

#### 1.4 Add Basic Tests

**Create: tests/test_common.sh**
```bash
#!/usr/bin/env bats

load '../lib/common.sh'

@test "timestamp returns formatted date" {
  result=$(timestamp)
  [[ "$result" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}:[0-9]{2}$ ]]
}

@test "redact_sensitive hides passwords" {
  result=$(redact_sensitive "password: mysecret123")
  [[ "$result" == *"[REDACTED]"* ]]
  [[ "$result" != *"mysecret123"* ]]
}

@test "command_exists returns 0 for valid command" {
  run command_exists "bash"
  [ "$status" -eq 0 ]
}

@test "command_exists returns 1 for invalid command" {
  run command_exists "nonexistentcommand12345"
  [ "$status" -eq 1 ]
}
```

---

## Phase 2: Configuration Module (Week 2)

### Goals
- Extract configuration logic
- Add validation
- Cache parsed configuration

### Tasks

#### 2.1 Create Configuration Module

**Create: lib/config.sh**
```bash
#!/bin/bash

# Configuration management module

# Configuration file path
CONFIGFILE="${CONFIGFILE:-/config/config.json}"

# Cached configuration
declare -A CONFIG_CACHE

# Load and validate configuration
load_config() {
  log "Loading configuration from ${CONFIGFILE}"
  
  if [ ! -f "${CONFIGFILE}" ]; then
    error "Configuration file not found: ${CONFIGFILE}"
    return 1
  fi
  
  if [ ! -r "${CONFIGFILE}" ]; then
    error "Cannot read configuration file: ${CONFIGFILE}"
    return 1
  fi
  
  # Validate JSON syntax
  if ! jq empty "${CONFIGFILE}" 2>/dev/null; then
    error "Invalid JSON in configuration file"
    return 1
  fi
  
  # Validate required fields
  if ! validate_config; then
    return 1
  fi
  
  log "Configuration loaded and validated successfully"
  return 0
}

# Validate configuration structure
validate_config() {
  log "Validating configuration..."
  
  # Check required settings
  local required_settings=(HOST SMTPSERVER SMTPPORT MAILFROM MAILTO)
  for setting in "${required_settings[@]}"; do
    if ! config_get_setting "${setting}" >/dev/null; then
      error "Missing required setting: ${setting}"
      return 1
    fi
  done
  
  # Validate jobs
  local jobs_count
  jobs_count=$(config_get_jobs_count)
  
  if [ "${jobs_count}" -eq 0 ]; then
    error "No jobs configured"
    return 1
  fi
  
  # Validate each job
  for ((i = 0; i < jobs_count; i++)); do
    if ! validate_job "${i}"; then
      return 1
    fi
  done
  
  log "Configuration validation passed"
  return 0
}

# Validate a single job
validate_job() {
  local idx="$1"
  
  local jobid jobtype crontab
  jobid=$(config_get_job_field "${idx}" "id")
  jobtype=$(config_get_job_field "${idx}" "type")
  crontab=$(config_get_job_field "${idx}" "crontab")
  
  if [ -z "${jobid}" ]; then
    error "Job ${idx}: missing id"
    return 1
  fi
  
  if [ -z "${jobtype}" ]; then
    error "Job ${jobid}: missing type"
    return 1
  fi
  
  if [[ ! "${jobtype}" =~ ^(s3bucket|azstorage|pgsql)$ ]]; then
    error "Job ${jobid}: invalid type '${jobtype}'"
    return 1
  fi
  
  if [ -z "${crontab}" ]; then
    error "Job ${jobid}: missing crontab"
    return 1
  fi
  
  # Validate cron pattern (basic check for 5 fields)
  local field_count
  field_count=$(echo "${crontab}" | wc -w)
  if [ "${field_count}" -ne 5 ]; then
    error "Job ${jobid}: invalid crontab pattern (must have 5 fields)"
    return 1
  fi
  
  return 0
}

# Get setting value
config_get_setting() {
  local key="$1"
  local value
  value=$(jq -r ".settings.${key}" "${CONFIGFILE}" | sed 's/^null$//')
  echo "${value}"
}

# Get number of jobs
config_get_jobs_count() {
  jq -r ".jobs | length" "${CONFIGFILE}"
}

# Get job field value
config_get_job_field() {
  local idx="$1"
  local field="$2"
  local value
  value=$(jq -r ".jobs[${idx}].${field}" "${CONFIGFILE}" | sed 's/^null$//')
  echo "${value}"
}

# Get all job IDs
config_get_job_ids() {
  jq -r ".jobs[].id" "${CONFIGFILE}"
}
```

#### 2.2 Update start.sh to Use Config Module

```bash
# After sourcing common.sh
source /usr/local/bin/lib/config.sh || exit 1

# Replace configuration loading (around line 397)
if ! load_config; then
  error "Failed to load configuration"
  exit 1
fi

# Replace setting reads with config functions
HOST=$(config_get_setting "HOST")
DEBUG=$(config_get_setting "DEBUG")
SMTPSERVER=$(config_get_setting "SMTPSERVER")
# etc.
```

---

## Phase 3: Job Execution Module (Week 3)

### Goals
- Extract job execution logic
- Reduce main loop complexity
- Improve testability

### Tasks

#### 3.1 Create Job Execution Module

**Create: lib/jobs.sh**
```bash
#!/bin/bash

# Job execution orchestration

# Execute a job by index
execute_job() {
  local job_idx="$1"
  local logfile="$2"
  
  local jobid jobtype jobdebug
  jobid=$(config_get_job_field "${job_idx}" "id")
  jobtype=$(config_get_job_field "${job_idx}" "type")
  jobdebug=$(config_get_job_field "${job_idx}" "debug")
  
  log "Executing job ${jobid} (type: ${jobtype})"
  
  local result=0
  case "${jobtype}" in
    s3bucket)
      execute_s3bucket_job "${job_idx}" "${jobid}" "${jobdebug}" "${logfile}"
      result=$?
      ;;
    azstorage)
      execute_azstorage_job "${job_idx}" "${jobid}" "${jobdebug}" "${logfile}"
      result=$?
      ;;
    pgsql)
      execute_pgsql_job "${job_idx}" "${jobid}" "${jobdebug}" "${logfile}"
      result=$?
      ;;
    *)
      error "Unknown job type: ${jobtype}"
      return 1
      ;;
  esac
  
  return ${result}
}

# Execute S3 bucket job
execute_s3bucket_job() {
  local job_idx="$1"
  local jobid="$2"
  local jobdebug="$3"
  local logfile="$4"
  
  local bucket_count result=0
  bucket_count=$(jq -r ".jobs[${job_idx}].buckets | length" "${CONFIGFILE}")
  
  if [ "${bucket_count}" -eq 0 ]; then
    log "Error: No buckets configured for job ${jobid}" >> "${logfile}"
    return 1
  fi
  
  for ((bucket_idx = 0; bucket_idx < bucket_count; bucket_idx++)); do
    local source destination delete_destination
    local aws_access_key_id aws_secret_access_key aws_region endpoint_url
    
    source=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].source" "${CONFIGFILE}")
    destination=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].destination" "${CONFIGFILE}")
    delete_destination=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].delete_destination" "${CONFIGFILE}")
    aws_access_key_id=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].aws_access_key_id" "${CONFIGFILE}")
    aws_secret_access_key=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].aws_secret_access_key" "${CONFIGFILE}")
    aws_region=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].aws_region" "${CONFIGFILE}")
    endpoint_url=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].endpoint_url" "${CONFIGFILE}")
    
    local script_args="-s ${source} -d ${destination} -m ${delete_destination}"
    script_args="${script_args} -a ${aws_access_key_id} -k ${aws_secret_access_key}"
    script_args="${script_args} -r ${aws_region} -e ${endpoint_url}"
    
    if [ "${jobdebug}" = "true" ]; then
      /bin/bash -x dump_s3bucket.sh ${script_args} >> "${logfile}" 2>&1
    else
      /bin/bash dump_s3bucket.sh ${script_args} >> "${logfile}" 2>&1
    fi
    
    local bucket_result=$?
    if [ ${bucket_result} -ne 0 ]; then
      result=${bucket_result}
    fi
  done
  
  return ${result}
}

# Similar functions for azstorage and pgsql...
```

---

## Phase 4: Scheduling Module (Week 4)

### Goals
- Extract cron matching logic
- Add scheduling tests
- Support more cron features

**Create: lib/scheduling.sh**
```bash
#!/bin/bash

# Job scheduling and cron pattern matching

# Check if cron pattern matches current time
check_schedule() {
  local cron_pattern="$1"
  
  local current_min current_hour current_day current_month current_dow
  current_min=$(date '+%-M')
  current_hour=$(date '+%-H')
  current_day=$(date '+%-d')
  current_month=$(date '+%-m')
  current_dow=$(date '+%u')
  
  # Convert Sunday from 7 to 0 for cron compatibility
  if [ "${current_dow}" = "7" ]; then
    current_dow="0"
  fi
  
  # Parse cron pattern
  read -r cron_min cron_hour cron_day cron_month cron_dow <<< "${cron_pattern}"
  
  # Check each field
  check_cron_field "${cron_min}" "${current_min}" && \
  check_cron_field "${cron_hour}" "${current_hour}" && \
  check_cron_field "${cron_day}" "${current_day}" && \
  check_cron_field "${cron_month}" "${current_month}" && \
  check_cron_field "${cron_dow}" "${current_dow}"
}

# Check if a cron field matches a value
check_cron_field() {
  local field="$1"
  local value="$2"
  
  # Wildcard
  if [ "${field}" = "*" ]; then
    return 0
  fi
  
  # Step values (*/5)
  if [[ "${field}" =~ ^\*/[0-9]+$ ]]; then
    local step="${field#*/}"
    if [ $((value % step)) -eq 0 ]; then
      return 0
    fi
    return 1
  fi
  
  # Ranges (1-5)
  if [[ "${field}" =~ ^[0-9]+-[0-9]+$ ]]; then
    local start="${field%-*}"
    local end="${field#*-}"
    if [ "${value}" -ge "${start}" ] && [ "${value}" -le "${end}" ]; then
      return 0
    fi
    return 1
  fi
  
  # Lists (1,3,5)
  if [[ "${field}" =~ , ]]; then
    local IFS=','
    for item in ${field}; do
      if [ "${item}" = "${value}" ]; then
        return 0
      fi
    done
    return 1
  fi
  
  # Exact match
  if [ "${field}" = "${value}" ]; then
    return 0
  fi
  
  return 1
}
```

---

## Phase 5: Email Module (Week 5)

**Create: lib/email.sh**
```bash
#!/bin/bash

# Email notification module

# Send startup email
send_startup_email() {
  local host="$1"
  local config_summary="$2"
  local jobs_summary="$3"
  
  local mail_body="CloudDump ${host}

STARTED

CONFIGURATION

${config_summary}

JOBS

${jobs_summary}

Vendanor CloudDump v${VERSION}"

  send_email "[Started] CloudDump ${host}" "${mail_body}"
}

# Send job completion email
send_job_email() {
  local jobid="$1"
  local result="$2"
  local start_time="$3"
  local end_time="$4"
  local logfile="$5"
  local config="$6"
  
  local result_text
  if [ "${result}" -eq 0 ]; then
    result_text="Success"
  else
    result_text="Failure"
  fi
  
  local elapsed_min=$(( (end_time - start_time) / 60 ))
  local elapsed_sec=$(( (end_time - start_time) % 60 ))
  
  local message="CloudDump ${HOST}

JOB REPORT (${result_text})

Job ID: ${jobid}
Started: ${start_time}
Completed: $(timestamp)
Time elapsed: ${elapsed_min} minutes ${elapsed_sec} seconds

CONFIGURATION

${config}

See attached logs for details.

Vendanor CloudDump v${VERSION}"

  send_email_with_attachments \
    "[${result_text}] CloudDump ${HOST}: ${jobid}" \
    "${message}" \
    "${logfile}"
}

# Send email (wrapper)
send_email() {
  local subject="$1"
  local body="$2"
  
  if [ "${MAIL}" = "mutt" ]; then
    echo "${body}" | EMAIL="${MAILFROM} <${MAILFROM}>" mutt -s "${subject}" "${MAILTO}"
  else
    echo "${body}" | mail -r "${MAILFROM} <${MAILFROM}>" -s "${subject}" "${MAILTO}"
  fi
}

# Send email with attachments
send_email_with_attachments() {
  local subject="$1"
  local body="$2"
  shift 2
  local attachments=("$@")
  
  local attach_args=""
  for file in "${attachments[@]}"; do
    if [ -f "${file}" ]; then
      attach_args="${attach_args} -a ${file}"
    fi
  done
  
  if [ "${MAIL}" = "mutt" ]; then
    echo "${body}" | EMAIL="${MAILFROM} <${MAILFROM}>" mutt -s "${subject}" ${attach_args} -- "${MAILTO}"
  else
    echo "${body}" | mail -r "${MAILFROM} <${MAILFROM}>" -s "${subject}" ${attach_args} "${MAILTO}"
  fi
}
```

---

## Phase 6: Mount Management (Week 6)

**Create: lib/mounts.sh**
```bash
#!/bin/bash

# Mount management module

# Mount all configured mounts
mount_all() {
  local mounts_count
  mounts_count=$(jq -r ".settings.mount | length" "${CONFIGFILE}")
  
  if [ "${mounts_count}" -eq 0 ]; then
    log "No mounts configured"
    return 0
  fi
  
  for ((i = 0; i < mounts_count; i++)); do
    if ! mount_entry "${i}"; then
      error "Failed to mount entry ${i}"
      return 1
    fi
  done
  
  return 0
}

# Mount a single entry
mount_entry() {
  local idx="$1"
  
  local path mountpoint username password privkey port
  path=$(jq -r ".settings.mount[${idx}].path" "${CONFIGFILE}")
  mountpoint=$(jq -r ".settings.mount[${idx}].mountpoint" "${CONFIGFILE}")
  username=$(jq -r ".settings.mount[${idx}].username" "${CONFIGFILE}")
  password=$(jq -r ".settings.mount[${idx}].password" "${CONFIGFILE}")
  privkey=$(jq -r ".settings.mount[${idx}].privkey" "${CONFIGFILE}")
  port=$(jq -r ".settings.mount[${idx}].port" "${CONFIGFILE}")
  
  if [[ "${path}" =~ : ]]; then
    mount_ssh "${path}" "${mountpoint}" "${username}" "${privkey}" "${port}"
  elif [[ "${path}" =~ ^// ]]; then
    mount_smb "${path}" "${mountpoint}" "${username}" "${password}"
  else
    error "Invalid mount path: ${path}"
    return 1
  fi
}

# Mount SSH filesystem
mount_ssh() {
  local path="$1"
  local mountpoint="$2"
  local username="$3"
  local privkey="$4"
  local port="$5"
  
  log "Mounting SSH: ${path} -> ${mountpoint}"
  
  # Setup SSH key if provided
  if [ -n "${privkey}" ]; then
    mkdir -p "${HOME}/.ssh"
    echo "${privkey}" > "${HOME}/.ssh/id_rsa"
    chmod 600 "${HOME}/.ssh/id_rsa"
  fi
  
  # Add username if not in path
  if [[ ! "${path}" =~ @ ]] && [ -n "${username}" ]; then
    path="${username}@${path}"
  fi
  
  mkdir -p "${mountpoint}"
  
  if [ -n "${port}" ]; then
    sshfs -o StrictHostKeyChecking=no -p "${port}" "${path}" "${mountpoint}"
  else
    sshfs -o StrictHostKeyChecking=no "${path}" "${mountpoint}"
  fi
}

# Mount SMB filesystem
mount_smb() {
  local path="$1"
  local mountpoint="$2"
  local username="$3"
  local password="$4"
  
  log "Mounting SMB: ${path} -> ${mountpoint}"
  
  # Extract host and share
  local smb_host smb_share smbnetfs_root
  smb_host="${path#//}"
  smb_host="${smb_host%%/*}"
  smb_share="${path#//${smb_host}/}"
  smbnetfs_root="/tmp/smbnetfs"
  
  # Mount smbnetfs if not already mounted
  if [ ! -d "${smbnetfs_root}/${smb_host}" ]; then
    mkdir -p "${smbnetfs_root}"
    
    if [ -n "${username}" ]; then
      # Create credentials
      local creds="/dev/shm/.smbcredentials"
      echo -e "${username}\n${password}" > "${creds}"
      chmod 600 "${creds}"
      
      # Mount with credentials
      echo "auth ${creds}" > /dev/shm/smbnetfs.conf
      smbnetfs "${smbnetfs_root}" -o config=/dev/shm/smbnetfs.conf,allow_other
    else
      # Guest access
      smbnetfs "${smbnetfs_root}" -o allow_other
    fi
    
    sleep 2
  fi
  
  # Create symlink
  ln -sf "${smbnetfs_root}/${smb_host}/${smb_share}" "${mountpoint}"
}
```

---

## Phase 7: Refactored start.sh (Week 7)

**New simplified start.sh:**
```bash
#!/bin/bash

# Vendanor CloudDump Startup Script
# Simplified main entry point

CONFIGFILE="/config/config.json"
VERSION=$(head -n 1 /VERSION)

# Source library modules
source /usr/local/bin/lib/common.sh || exit 1
source /usr/local/bin/lib/config.sh || exit 1
source /usr/local/bin/lib/scheduling.sh || exit 1
source /usr/local/bin/lib/jobs.sh || exit 1
source /usr/local/bin/lib/email.sh || exit 1
source /usr/local/bin/lib/mounts.sh || exit 1

# Enable debug if configured
if [ "$(config_get_setting 'DEBUG')" = "true" ]; then
  set -x
fi

# Main initialization
main_init() {
  log "Vendanor CloudDump v${VERSION} Start"
  
  # Set up signal handlers
  trap 'shutdown_handler' SIGTERM SIGINT
  
  # Check required commands
  if ! check_required_commands grep sed cut cp chmod mkdir bc jq mail mutt postconf postmap ssh sshfs smbnetfs lockfile; then
    exit 1
  fi
  
  # Load and validate configuration
  if ! load_config; then
    exit 1
  fi
  
  # Setup SMTP
  if ! setup_smtp; then
    exit 1
  fi
  
  # Mount filesystems
  if ! mount_all; then
    exit 1
  fi
  
  # Send startup email
  send_startup_email "$(config_get_setting 'HOST')" "..." "..."
  
  log "Initialization complete"
}

# Main loop
main_loop() {
  log "Starting main loop..."
  
  declare -A last_run_times
  
  while true; do
    local current_minute
    current_minute=$(date '+%Y-%m-%d %H:%M')
    
    local jobs_count
    jobs_count=$(config_get_jobs_count)
    
    for ((i = 0; i < jobs_count; i++)); do
      process_job "${i}" "${current_minute}" last_run_times
    done
    
    sleep_until_next_minute
  done
}

# Process a single job
process_job() {
  local job_idx="$1"
  local current_minute="$2"
  local -n last_runs="$3"
  
  local jobid crontab
  jobid=$(config_get_job_field "${job_idx}" "id")
  crontab=$(config_get_job_field "${job_idx}" "crontab")
  
  # Initialize last run time
  if [ -z "${last_runs[${jobid}]}" ]; then
    last_runs[${jobid}]="0"
  fi
  
  # Check if schedule matches
  if ! check_schedule "${crontab}"; then
    return 0
  fi
  
  # Check if already ran this minute
  local last_run_minute
  last_run_minute=$(date -d "@${last_runs[${jobid}]}" '+%Y-%m-%d %H:%M' 2>/dev/null)
  
  if [ "${last_run_minute}" = "${current_minute}" ]; then
    return 0
  fi
  
  # Acquire lock
  local lockfile="/tmp/LOCKFILE_${jobid}"
  if ! lockfile -r 0 "${lockfile}" 2>/dev/null; then
    log "Job ${jobid} already running, skipping"
    return 0
  fi
  
  # Execute job
  local logfile="/tmp/vnclouddump-${jobid}-$$.log"
  local start_time end_time result
  
  start_time=$(date +%s)
  execute_job "${job_idx}" "${logfile}"
  result=$?
  end_time=$(date +%s)
  
  # Send report
  local config
  config=$(get_job_configuration "${jobid}")
  send_job_email "${jobid}" "${result}" "${start_time}" "${end_time}" "${logfile}" "${config}"
  
  # Cleanup
  rm -f "${logfile}" "${lockfile}"
  
  # Update last run time
  last_runs[${jobid}]=$(date +%s)
}

# Sleep until next minute
sleep_until_next_minute() {
  local current_second
  current_second=$(date '+%-S')
  local sleep_seconds=$((60 - current_second))
  
  if [ "${sleep_seconds}" -le 0 ]; then
    sleep_seconds=1
  fi
  
  sleep "${sleep_seconds}"
}

# Graceful shutdown
shutdown_handler() {
  log "Received shutdown signal, exiting gracefully..."
  exit 0
}

# Run main program
main_init
main_loop
```

---

## Migration Strategy

### Approach
Incremental migration with backward compatibility:

1. **Add new modules** alongside existing code
2. **Update start.sh** to use new modules
3. **Keep old code** temporarily as fallback
4. **Test thoroughly** at each step
5. **Remove old code** only when confident

### Testing at Each Phase
```bash
# Syntax check
bash -n start.sh lib/*.sh

# Shellcheck
shellcheck start.sh lib/*.sh

# Unit tests
bats tests/*.sh

# Integration test
docker build -t clouddump:test .
docker run --rm -v $(pwd)/test-config.json:/config/config.json clouddump:test
```

---

## Benefits After Refactoring

### Maintainability
- ✅ Smaller, focused files (<200 lines each)
- ✅ Clear separation of concerns
- ✅ Easier to understand and modify
- ✅ Reduced code duplication

### Testability
- ✅ Unit tests for each module
- ✅ Integration tests for workflows
- ✅ Mocking capabilities for external dependencies
- ✅ CI/CD integration

### Extensibility
- ✅ Easy to add new job types
- ✅ Simple to add new notification channels
- ✅ Pluggable mount types
- ✅ Configurable scheduling backends

### Reliability
- ✅ Better error handling
- ✅ Configuration validation
- ✅ Health checking
- ✅ Automated testing

---

## Success Metrics

- [ ] All shellcheck warnings resolved
- [ ] Test coverage >80%
- [ ] All files <300 lines
- [ ] No code duplication
- [ ] Configuration validation prevents 90% of user errors
- [ ] Zero regression bugs
- [ ] Build time <5 minutes
- [ ] CI/CD pipeline passing
