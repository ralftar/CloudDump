#!/bin/bash

# Vendanor CloudDump Startup Script
# This script reads Json configuration and runs jobs in a single loop


CONFIGFILE="/config/config.json"
MAIL="mutt"

VERSION=$(head -n 1 /VERSION)


if [ "$(jq -r '.settings.DEBUG' ${CONFIGFILE})" = "true" ]; then
  set -x
fi


# Functions

# Gets a formatted timestamp string for logging purposes
#
# Returns:
#   Current date and time in 'YYYY-MM-DD HH:MM:SS' format
#
get_timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

# Logs a message to stdout with timestamp prefix
#
# Arguments:
#   All arguments are concatenated and logged as the message
#
# Output:
#   [YYYY-MM-DD HH:MM:SS] message
#
log() {
  echo "[$(get_timestamp)] $*"
}

# Logs an error message to stderr with timestamp and ERROR prefix
#
# Arguments:
#   All arguments are concatenated and logged as the error message
#
# Output:
#   [YYYY-MM-DD HH:MM:SS] ERROR: message (sent to stderr)
#
log_error() {
  echo "[$(get_timestamp)] ERROR: $*" >&2
}

# Converts a JSON array to a space-separated string
#
# Reads a JSON array from the configuration file and converts it to a
# space-separated string of values, skipping null or empty entries.
#
# Arguments:
#   $1 - jq_path: The jq path expression to the JSON array in CONFIGFILE
#
# Returns:
#   Space-separated string of array values via stdout
#
# Example:
#   converts_json_array_to_string ".settings.mount"
#
converts_json_array_to_string() {
  local jq_path="$1"
  local array_index
  local output_string=""
  local array_length
  
  array_length=$(jq -r "${jq_path} | length" "${CONFIGFILE}")
  
  for ((array_index = 0; array_index < array_length; array_index++)); do
    local array_value
    if ! array_value=$(jq -r "${jq_path}[${array_index}]" "${CONFIGFILE}" | sed 's/^null$//g') || [ "${array_value}" = "" ]; then
      continue
    fi
    if [ "${output_string}" = "" ]; then
      output_string="${array_value}"
    else
      output_string="${output_string} ${array_value}"
    fi
  done

  echo "${output_string}"
}

# Removes sensitive information from text for safe logging and display
#
# Redacts passwords, keys, tokens, secrets, and Azure SAS token parameters
# from text to prevent exposure in logs and emails.
#
# Arguments:
#   $1 - text_to_redact: The text containing potentially sensitive information
#
# Returns:
#   Sanitized text with sensitive values replaced with [REDACTED]
#
# Example:
#   sanitized=$(removes_sensitive_data "password=secret123")
#   # Returns: "password: [REDACTED]"
#
removes_sensitive_data() {
  local text_to_redact="$1"
  # Redact common sensitive field patterns (password, key, token, secret)
  text_to_redact=$(echo "${text_to_redact}" | sed 's/\(password\|pass\|key\|token\|secret\)[[:space:]]*[:=][[:space:]]*[^[:space:]]*/\1: [REDACTED]/gi')
  # Redact Azure SAS token parameters from URLs
  text_to_redact=$(echo "${text_to_redact}" | sed 's/\?[^?]*\(sig\|se\|st\|sp\)=[^&?]*/\?[REDACTED]/g')
  echo "${text_to_redact}"
}

# Handles graceful shutdown when termination signals are received
#
# This handler is called when SIGTERM or SIGINT signals are received,
# allowing the application to log the shutdown and exit cleanly.
#
handles_shutdown_signal() {
  log "Received shutdown signal, exiting gracefully..."
  exit 0
}

# Formats job configuration as readable text with sensitive data removed
#
# Retrieves a job's configuration from the JSON config file and formats it
# for display in emails or logs. Automatically redacts sensitive information
# like passwords, access keys, and SAS tokens.
#
# Arguments:
#   $1 - job_index: The numeric index of the job in the .jobs array
#
# Returns:
#   Formatted configuration text via stdout, or "Configuration unavailable" on error
#
# Example:
#   job_config=$(formats_job_configuration_for_display 0)
#
formats_job_configuration_for_display() {
  local job_index="$1"
  
  # Use jq to extract and format the job configuration, removing sensitive fields
  jq -r ".jobs[${job_index}] | 
    # Remove sensitive password/key fields from output
    del(.buckets[]?.aws_access_key_id, .buckets[]?.aws_secret_access_key) |
    del(.servers[]?.pass) |
    # Strip SAS tokens from Azure Storage URLs
    if .blobstorages then
      .blobstorages = [.blobstorages[] | .source = (.source | split(\"?\")[0])]
    else . end |
    # Convert to YAML-like format for readability
    to_entries |
    map(
      if .key == \"blobstorages\" or .key == \"buckets\" or .key == \"servers\" then
        \"\(.key):\\n\" + (.value | to_entries | map(\"  [\(.key)]\\n\" + (.value | to_entries | map(\"    \(.key): \(.value)\") | join(\"\\n\"))) | join(\"\\n\"))
      else
        \"\(.key): \(.value)\"
      end
    ) |
    join(\"\\n\")" "${CONFIGFILE}" 2>/dev/null || echo "Configuration unavailable"
}

# Sends an email report for a completed job execution
#
# Constructs and sends an email containing job execution results, timing information,
# configuration details, and log files as attachments. Includes azcopy log files
# if they are referenced in the main log file.
#
# Arguments:
#   $1 - job_identifier: The unique ID of the job
#   $2 - script_name: Name of the script that was executed
#   $3 - exit_code: Exit code from the job execution (0=success, non-zero=failure)
#   $4 - start_unix_timestamp: Unix timestamp when job started
#   $5 - end_unix_timestamp: Unix timestamp when job completed
#   $6 - start_formatted_timestamp: Human-readable start time
#   $7 - log_file_path: Path to the job's log file
#   $8 - job_configuration: Formatted configuration text for the job
#
# Example:
#   sends_job_completion_email "backup1" "dump_s3bucket.sh" 0 1704067200 1704067800 "2024-01-01 00:00:00" "/tmp/job.log" "$config"
#
sends_job_completion_email() {
  local job_identifier="$1"
  local script_name="$2"
  local exit_code="$3"
  local start_unix_timestamp="$4"
  local end_unix_timestamp="$5"
  local start_formatted_timestamp="$6"
  local log_file_path="$7"
  local job_configuration="$8"
  
  local result_status_text
  if [ "${exit_code}" -eq 0 ]; then
    result_status_text="Success"
  else
    result_status_text="Failure"
  fi
  
  local script_filename
  if echo "${script_name}" | grep '\/' >/dev/null 2>&1; then
    script_filename=$(echo "${script_name}" | sed 's/.*\///g')
  else
    script_filename="${script_name}"
  fi
  
  log "Sending e-mail to ${MAILTO} from ${MAILFROM} for job ${job_identifier}."
  
  # Determine the correct attachment option flag based on mail command type
  local mail_attachment_option
  if [ "${MAIL}" = "mail" ]; then
    if "${MAIL}" -V >/dev/null 2>&1; then
      if "${MAIL}" -V | grep "^mail (GNU Mailutils)" >/dev/null 2>&1; then
        mail_attachment_option="-A"
      else
        mail_attachment_option="-a"
      fi
    else
      mail_attachment_option="-A"
    fi
  elif [ "${MAIL}" = "mutt" ]; then
    mail_attachment_option="-a"
  else
    log "Unknown mail command: ${MAIL}"
    return 1
  fi
  
  local email_attachments="${mail_attachment_option} ${log_file_path}"
  
  # Locate and attach any azcopy log files referenced in the main log
  if [ -f "${log_file_path}" ]; then
    local azcopy_log_files
    azcopy_log_files=$(grep '^Log file is located at: .*\.log$' "${log_file_path}" | sed -e 's/Log file is located at: \(.*\)/\1/' | sed 's/\r$//' | tr '\n' ' ' | sed 's/ $//g')
    if ! [ "${azcopy_log_files}" = "" ]; then
      for azcopy_log_file in ${azcopy_log_files}; do
        if [ ! "${azcopy_log_file}" = "" ] && [ -f "${azcopy_log_file}" ]; then
          email_attachments="${email_attachments} ${mail_attachment_option} ${azcopy_log_file}"
        fi
      done
    fi
  fi
  
  email_attachments="${email_attachments} --"
  
  local email_message
  email_message="CloudDump ${HOST}

JOB REPORT (${result_status_text})

Script: ${script_filename}
ID: ${job_identifier}
Started: ${start_formatted_timestamp}
Completed: $(get_timestamp)
Time elapsed: $(((end_unix_timestamp - start_unix_timestamp)/60)) minutes $(((end_unix_timestamp - start_unix_timestamp)%60)) seconds

CONFIGURATION

${job_configuration}

For more information consult the attached logs.

Vendanor CloudDump v${VERSION}
"
  
  if [ "${MAIL}" = "mutt" ]; then
    # shellcheck disable=SC2086
    echo "${email_message}" | EMAIL="${MAILFROM} <${MAILFROM}>" "${MAIL}" -s "[${result_status_text}] CloudDump ${HOST}: ${job_identifier}" ${email_attachments} "${MAILTO}"
  else
    # shellcheck disable=SC2086
    echo "${email_message}" | "${MAIL}" -r "${MAILFROM} <${MAILFROM}>" -s "[${result_status_text}] CloudDump ${HOST}: ${job_identifier}" ${email_attachments} "${MAILTO}"
  fi
}

# Retrieves and formats job configuration by job identifier
#
# Searches for a job by its unique ID in the configuration file and returns
# its formatted configuration for use in email reports.
#
# Arguments:
#   $1 - job_identifier: The unique ID of the job to retrieve
#
# Returns:
#   Formatted job configuration via stdout, or empty string if not found
#   Exit code 0 on success, 1 if job not found
#
# Example:
#   config=$(retrieves_job_configuration_by_id "backup1")
#
retrieves_job_configuration_by_id() {
  local job_identifier="$1"
  
  # Determine total number of jobs in configuration
  local total_jobs job_array_index
  total_jobs=$(jq -r ".jobs | length" "${CONFIGFILE}")
  if [ "${total_jobs}" = "" ] || [ -z "${total_jobs}" ] || ! [ "${total_jobs}" -eq "${total_jobs}" ] 2>/dev/null; then
    echo ""
    return 1
  fi

  job_array_index=
  for ((i = 0; i < total_jobs; i++)); do
    local current_job_id
    if ! current_job_id=$(jq -r ".jobs[${i}].id" "${CONFIGFILE}" | sed 's/^null$//g') || [ "${current_job_id}" = "" ]; then
      continue
    fi
    if [ "${current_job_id}" = "${job_identifier}" ]; then
      job_array_index="${i}"
      break
    fi
  done

  if [ "${job_array_index}" = "" ]; then
    echo ""
    return 1
  fi

  # Format and return the job's configuration
  formats_job_configuration_for_display "${job_array_index}"
}


# Helper function to execute s3bucket job
execute_s3bucket_job() {
  local job_idx="$1"
  local jobid="$2"
  local jobdebug="$3"
  local logfile="$4"
  
  local bucket_count result
  result=0
  bucket_count=$(jq -r ".jobs[${job_idx}].buckets | length" "${CONFIGFILE}")
  if [ "${bucket_count}" = "" ] || [ -z "${bucket_count}" ] || ! [ "${bucket_count}" -eq "${bucket_count}" ] 2>/dev/null; then
    log "Error: Can't read buckets from Json configuration for job ${jobid}." >> "${logfile}"
    return 1
  elif [ "${bucket_count}" -eq 0 ]; then
    log "Error: No buckets for ${jobid} in Json configuration." >> "${logfile}"
    return 1
  fi
  
  for ((bucket_idx = 0; bucket_idx < bucket_count; bucket_idx++)); do
    local source destination delete_destination aws_access_key_id aws_secret_access_key aws_region endpoint_url bucket_result
    source=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].source" "${CONFIGFILE}" | sed 's/^null$//g')
    destination=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].destination" "${CONFIGFILE}" | sed 's/^null$//g')
    delete_destination=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].delete_destination" "${CONFIGFILE}" | sed 's/^null$//g')
    aws_access_key_id=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].aws_access_key_id" "${CONFIGFILE}" | sed 's/^null$//g')
    aws_secret_access_key=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].aws_secret_access_key" "${CONFIGFILE}" | sed 's/^null$//g')
    aws_region=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].aws_region" "${CONFIGFILE}" | sed 's/^null$//g')
    endpoint_url=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].endpoint_url" "${CONFIGFILE}" | sed 's/^null$//g')
    
    if [ "${jobdebug}" = "true" ]; then
      /bin/bash -x dump_s3bucket.sh "${source}" "${destination}" "${delete_destination}" "${aws_access_key_id}" "${aws_secret_access_key}" "${aws_region}" "${endpoint_url}" >> "${logfile}" 2>&1
      bucket_result=$?
    else
      /bin/bash dump_s3bucket.sh "${source}" "${destination}" "${delete_destination}" "${aws_access_key_id}" "${aws_secret_access_key}" "${aws_region}" "${endpoint_url}" >> "${logfile}" 2>&1
      bucket_result=$?
    fi
    
    if [ ${bucket_result} -ne 0 ]; then
      result=${bucket_result}
    fi
  done
  
  return ${result}
}

# Helper function to execute azstorage job
execute_azstorage_job() {
  local job_idx="$1"
  local jobid="$2"
  local jobdebug="$3"
  local logfile="$4"
  
  local bs_count result
  result=0
  bs_count=$(jq -r ".jobs[${job_idx}].blobstorages | length" "${CONFIGFILE}")
  if [ "${bs_count}" = "" ] || [ -z "${bs_count}" ] || ! [ "${bs_count}" -eq "${bs_count}" ] 2>/dev/null; then
    log "Error: Can't read blobstorages from Json configuration for job ${jobid}." >> "${logfile}"
    return 1
  elif [ "${bs_count}" -eq 0 ]; then
    log "Error: No blobstorages for ${jobid} in Json configuration." >> "${logfile}"
    return 1
  fi
  
  for ((bs_idx = 0; bs_idx < bs_count; bs_idx++)); do
    local source destination delete_destination bs_result
    source=$(jq -r ".jobs[${job_idx}].blobstorages[${bs_idx}].source" "${CONFIGFILE}" | sed 's/^null$//g')
    destination=$(jq -r ".jobs[${job_idx}].blobstorages[${bs_idx}].destination" "${CONFIGFILE}" | sed 's/^null$//g')
    delete_destination=$(jq -r ".jobs[${job_idx}].blobstorages[${bs_idx}].delete_destination" "${CONFIGFILE}" | sed 's/^null$//g')
    
    if [ "${jobdebug}" = "true" ]; then
      /bin/bash -x dump_azstorage.sh "${source}" "${destination}" "${delete_destination}" >> "${logfile}" 2>&1
      bs_result=$?
    else
      /bin/bash dump_azstorage.sh "${source}" "${destination}" "${delete_destination}" >> "${logfile}" 2>&1
      bs_result=$?
    fi
    
    if [ ${bs_result} -ne 0 ]; then
      result=${bs_result}
    fi
  done
  
  return ${result}
}

# Helper function to execute pgsql job
execute_pgsql_job() {
  local job_idx="$1"
  local jobid="$2"
  local jobdebug="$3"
  local logfile="$4"
  
  local server_count result
  result=0
  server_count=$(jq -r ".jobs[${job_idx}].servers | length" "${CONFIGFILE}")
  if [ "${server_count}" = "" ] || [ -z "${server_count}" ] || ! [ "${server_count}" -eq "${server_count}" ] 2>/dev/null; then
    log "Error: Can't read servers from Json configuration for job ${jobid}." >> "${logfile}"
    return 1
  elif [ "${server_count}" -eq 0 ]; then
    log "Error: No servers for ${jobid} in Json configuration." >> "${logfile}"
    return 1
  fi
  
  for ((server_idx = 0; server_idx < server_count; server_idx++)); do
    local PGHOST PGPORT PGUSERNAME PGPASSWORD backuppath filenamedate compress server_result
    local databases_json databases_excluded_json
    
    PGHOST=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].host" "${CONFIGFILE}" | sed 's/^null$//g')
    PGPORT=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].port" "${CONFIGFILE}" | sed 's/^null$//g')
    PGUSERNAME=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].user" "${CONFIGFILE}" | sed 's/^null$//g')
    PGPASSWORD=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].pass" "${CONFIGFILE}" | sed 's/^null$//g')
    backuppath=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].backuppath" "${CONFIGFILE}" | sed 's/^null$//g')
    filenamedate=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].filenamedate" "${CONFIGFILE}" | sed 's/^null$//g')
    compress=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].compress" "${CONFIGFILE}" | sed 's/^null$//g')
    
    # Pass the databases and databases_excluded configuration as JSON to the dump script via environment variables
    databases_json=$(jq -c ".jobs[${job_idx}].servers[${server_idx}].databases // []" "${CONFIGFILE}")
    databases_excluded_json=$(jq -c ".jobs[${job_idx}].servers[${server_idx}].databases_excluded // []" "${CONFIGFILE}")
    
    if [ "${jobdebug}" = "true" ]; then
      DATABASES_JSON="${databases_json}" DATABASES_EXCLUDED_JSON="${databases_excluded_json}" /bin/bash -x dump_pgsql.sh -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSERNAME}" -P "${PGPASSWORD}" -b "${backuppath}" -f "${filenamedate}" -z "${compress}" >> "${logfile}" 2>&1
      server_result=$?
    else
      DATABASES_JSON="${databases_json}" DATABASES_EXCLUDED_JSON="${databases_excluded_json}" /bin/bash dump_pgsql.sh -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSERNAME}" -P "${PGPASSWORD}" -b "${backuppath}" -f "${filenamedate}" -z "${compress}" >> "${logfile}" 2>&1
      server_result=$?
    fi
    
    if [ ${server_result} -ne 0 ]; then
      result=${server_result}
    fi
  done
  
  return ${result}
}


# Init

mkdir -p /persistent-data/logs

log "Vendanor CloudDump v${VERSION} Start ($0)"

# Set up signal handlers
trap 'handles_shutdown_signal' SIGTERM SIGINT


# Check commands

cmds="which grep sed cut cp chmod mkdir bc jq mail mutt postconf postmap ssh sshfs smbnetfs"
cmds_missing=
for cmd in ${cmds}
do
  if which "${cmd}" >/dev/null 2>&1 ; then
    continue
  fi
  if [ "${cmds_missing}" = "" ]; then
    cmds_missing="${cmd}"
  else
    cmds_missing="${cmds_missing} ${cmd}"
  fi
done

if ! [ "${cmds_missing}" = "" ]; then
  log_error "Missing \"${cmds_missing}\" commands."
  exit 1
fi


# Read settings

if [ ! -f "${CONFIGFILE}" ]; then
  log_error "Missing Json configuration file ${CONFIGFILE}."
  exit 1
fi

if [ ! -r "${CONFIGFILE}" ]; then
  log_error "Can't read Json configuration file ${CONFIGFILE}."
  exit 1
fi

HOST=$(jq -r '.settings.HOST' "${CONFIGFILE}" | sed 's/^null$//g')
DEBUG=$(jq -r '.settings.DEBUG' "${CONFIGFILE}")

log "CONFIGURATION:"
log "Host: $HOST"


# Setup postfix and mutt
SMTPSERVER=$(jq -r '.settings.SMTPSERVER' "${CONFIGFILE}" | sed 's/^null$//g')
SMTPPORT=$(jq -r '.settings.SMTPPORT' "${CONFIGFILE}" | sed 's/^null$//g')
SMTPUSER=$(jq -r '.settings.SMTPUSER' "${CONFIGFILE}" | sed 's/^null$//g')
SMTPPASS=$(jq -r '.settings.SMTPPASS' "${CONFIGFILE}" | sed 's/^null$//g')
MAILFROM=$(jq -r '.settings.MAILFROM' "${CONFIGFILE}" | sed 's/^null$//g')
MAILTO=$(jq -r '.settings.MAILTO' "${CONFIGFILE}" | sed 's/^null$//g')

postconf maillog_file=/var/log/postfix.log || exit 1
postconf inet_interfaces=127.0.0.1 || exit 1
postconf relayhost="[${SMTPSERVER}]:${SMTPPORT}" || exit 1
postconf smtp_sasl_auth_enable=yes || exit 1
postconf smtp_sasl_password_maps=lmdb:/etc/postfix/sasl_passwd || exit 1
postconf smtp_tls_wrappermode=yes || exit 1
postconf smtp_tls_security_level=encrypt || exit 1
postconf smtp_sasl_security_options=noanonymous || exit 1

touch /etc/postfix/relay || exit 1
chmod 600 /etc/postfix/relay || exit 1
touch /etc/postfix/sasl_passwd || exit 1
chmod 600 /etc/postfix/sasl_passwd || exit 1
touch /etc/Muttrc || exit 1

if ! [ "${SMTPSERVER}" = "" ] && ! [ "${SMTPPORT}" = "" ]; then
  log "SMTP server: $SMTPSERVER"
  log "SMTP port: $SMTPPORT"
  log "SMTP username: $SMTPUSER"
  if [ "$SMTPUSER" = "" ] && [ "$SMTPPASS" = "" ]; then
    SMTPURL="smtps://${SMTPSERVER}:${SMTPPORT}"
  else
    SMTPURL="smtps://${SMTPUSER}:${SMTPPASS}@${SMTPSERVER}:${SMTPPORT}"
    if ! grep "^\[${SMTPSERVER}\]:${SMTPPORT} ${SMTPUSER}:${SMTPPASS}$" /etc/postfix/sasl_passwd >/dev/null; then
      echo "[${SMTPSERVER}]:${SMTPPORT} ${SMTPUSER}:${SMTPPASS}" >> /etc/postfix/sasl_passwd || exit 1
    fi
  fi
  if ! grep "^set smtp_url=\"${SMTPURL}\"$" /etc/Muttrc >/dev/null; then
    echo "set smtp_url=\"${SMTPURL}\"" >> /etc/Muttrc || exit 1
  fi
fi

postmap /etc/postfix/relay || exit 1
postmap lmdb:/etc/postfix/sasl_passwd || exit 1

# Start postfix if not already running
if ! pgrep -x master >/dev/null 2>&1; then
  /usr/sbin/postfix start || exit 1
else
  log "Postfix already running, reloading configuration..."
  /usr/sbin/postfix reload || exit 1
fi


# Mount

mounts=$(jq -r ".settings.mount | length" "${CONFIGFILE}")
if [ "${mounts}" -gt 0 ]; then
  for ((i = 0; i < mounts; i++)); do
    if ! path=$(jq -r ".settings.mount[${i}].path" "${CONFIGFILE}" | sed 's/^null$//g' | sed 's/\\/\//g') || [ "${path}" = "" ]; then
      continue
    fi
    if ! mountpoint=$(jq -r ".settings.mount[${i}].mountpoint" "${CONFIGFILE}" | sed 's/^null$//g') || [ "${mountpoint}" = "" ]; then
      continue
    fi
    username=$(jq -r ".settings.mount[${i}].username" "${CONFIGFILE}" | sed 's/^null$//g')
    privkey=$(jq -r ".settings.mount[${i}].privkey" "${CONFIGFILE}" | sed 's/^null$//g')
    password=$(jq -r ".settings.mount[${i}].password" "${CONFIGFILE}" | sed 's/^null$//g')
    port=$(jq -r ".settings.mount[${i}].port" "${CONFIGFILE}" | sed 's/^null$//g')

    mount_summary="
Path: ${path}
Mountpoint ${mountpoint}
"

  if [ "${mounts_summary}" = "" ]; then
    mounts_summary="${mount_summary}"
  else
    mounts_summary="${mounts_summary}
${mount_summary}"
  fi

    if echo "${path}" | grep ':' >/dev/null 2>&1; then # SSH
      if [ ! "${privkey}" = "" ]; then
        mkdir -p "${HOME}/.ssh" || exit 1
        # Add cleanup trap
        trap 'rm -f "${HOME}/.ssh/id_rsa"' EXIT
        echo "${privkey}" >"${HOME}/.ssh/id_rsa" || exit 1
        chmod 600 "${HOME}/.ssh/id_rsa" || exit 1
      fi
      if ! echo "${path}" | grep '@' >/dev/null 2>&1 && ! [ "${username}" = "" ]; then
        path="${username}@${path}"
      fi
      log "Mounting ${path} to ${mountpoint} using sshfs."
      mkdir -p "${mountpoint}" || exit 1
      if [ "${port}" = "" ]; then
        sshfs -v -o StrictHostKeyChecking=no "${path}" "${mountpoint}" || exit 1
      else
        sshfs -v -o StrictHostKeyChecking=no -p "${port}" "${path}" "${mountpoint}" || exit 1
      fi
      continue
    fi
    if echo "${path}" | grep '^\/\/' >/dev/null 2>&1; then # SMB
      # Extract host and share from path (//host/share)
      smb_host=$(echo "${path}" | sed 's|^//\([^/]*\)/.*|\1|')
      smb_share=$(echo "${path}" | sed 's|^//[^/]*/\(.*\)|\1|')
      
      log "Mounting ${path} to ${mountpoint} using smbnetfs."
      
      # Use a single shared smbnetfs root for all mounts
      smbnetfs_root="/tmp/smbnetfs"
      
      # Mount smbnetfs if not already mounted
      if [ ! -d "${smbnetfs_root}/${smb_host}" ]; then
        mkdir -p "${smbnetfs_root}" || exit 1
        
        # Create credentials file if username is provided
        if [ ! "${username}" = "" ]; then
          mkdir -p /dev/shm || exit 1
          smbcredentials="/dev/shm/.smbcredentials"
          # Add cleanup trap
          trap 'rm -f "${smbcredentials}" /dev/shm/smbnetfs.conf' EXIT
          if [ "${password}" = "" ]; then
            echo -e "${username}\n" > "${smbcredentials}"
          else
            echo -e "${username}\n${password}" > "${smbcredentials}"
          fi
          chmod 600 "${smbcredentials}" || exit 1
          
          # Create config file
          echo "auth ${smbcredentials}" > /dev/shm/smbnetfs.conf || exit 1
          smbnetfs "${smbnetfs_root}" -o config=/dev/shm/smbnetfs.conf,allow_other || exit 1
        else
          # Mount without credentials for guest access
          smbnetfs "${smbnetfs_root}" -o allow_other || exit 1
        fi
        
        sleep 2
      fi
      
      # Create a symlink to the actual share path
      ln -sf "${smbnetfs_root}/${smb_host}/${smb_share}" "${mountpoint}" || exit 1
      
      continue
    fi
    log_error "Invalid path ${path} for mountpoint ${mountpoint}."
    log_error "Syntax is \"user@host:/path\" for SSH, or \"//host/path\" for SMB."
    exit 1
  done
fi


# Read and validate jobs configuration

jobs=$(jq -r ".jobs | length" "${CONFIGFILE}")
if [ "${jobs}" = "" ] || [ -z "${jobs}" ] || ! [ "${jobs}" -eq "${jobs}" ] 2>/dev/null; then
  log_error "Can't read jobs from Json configuration."
  exit 1
fi

if [ "${jobs}" -eq 0 ]; then
  log_error "No jobs in Json configuration."
  exit 1
fi

# Build job summary for startup email
jobs_summary=""
for ((i = 0; i < jobs; i++)); do

  if ! jobid=$(jq -r ".jobs[${i}].id" "${CONFIGFILE}" | sed 's/^null$//g') || [ "${jobid}" = "" ]; then
    log_error "Missing job ID for job index ${i}."
    continue
  fi

  if ! type=$(jq -r ".jobs[${i}].type" "${CONFIGFILE}" | sed 's/^null$//g') || [ "${type}" = "" ]; then
    log_error "Missing type for job ID ${jobid}."
    continue
  fi
  
  script="dump_${type}.sh"

  if ! crontab=$(jq -r ".jobs[${i}].crontab" "${CONFIGFILE}" | sed 's/^null$//g') || [ "${crontab}" = "" ]; then
    log_error "Missing crontab for job ID ${jobid}."
    continue
  fi

  jobdebug=$(jq -r ".jobs[${i}].debug" "${CONFIGFILE}")

  if echo "${script}" | grep '^\/' >/dev/null 2>&1; then
    scriptfile="${script}"
  else
    scriptfile=$(which "${script}" 2>/dev/null)
    if [ "${scriptfile}" = "" ]; then
      log_error "Missing scriptfile ${script}."
      exit 1
    fi
  fi

  if ! [ -f "${scriptfile}" ]; then
    log_error "Missing scriptfile ${scriptfile}."
    exit 1
  fi

  if ! [ -x "${scriptfile}" ]; then
    log_error "Scriptfile ${scriptfile} not executable."
    exit 1
  fi

  job_summary="ID: ${jobid}
Script: ${script}
Schedule: ${crontab}
Debug: ${jobdebug}"

  if [ "${jobs_summary}" = "" ]; then
    jobs_summary="${job_summary}"
  else
    jobs_summary="${jobs_summary}

${job_summary}"
  fi

done


# Send startup e-mail

# Build comprehensive startup configuration (with redaction)
startup_config="Debug: ${DEBUG}
SMTP server: ${SMTPSERVER}
SMTP port: ${SMTPPORT}
Mail from: ${MAILFROM}
Mail to: ${MAILTO}"

# Add mount summary if present
if [ ! "${mounts_summary}" = "" ]; then
  startup_config="${startup_config}

Mountpoints:
${mounts_summary}"
fi

# Add jobs count
startup_config="${startup_config}

Total jobs configured: ${jobs}"

# Redact sensitive information from the entire mail body
startup_config=$(removes_sensitive_data "${startup_config}")
jobs_summary=$(removes_sensitive_data "${jobs_summary}")

mail_body="CloudDump ${HOST}

STARTED

CONFIGURATION

${startup_config}

JOBS

${jobs_summary}

Vendanor CloudDump v${VERSION}"

if [ "${MAIL}" = "mutt" ]; then
  echo "${mail_body}" | EMAIL="${MAILFROM} <${MAILFROM}>" ${MAIL} -s "[Started] CloudDump ${HOST}" "${MAILTO}"
else
  echo "${mail_body}" | ${MAIL} -r "${MAILFROM} <${MAILFROM}>" -s "[Started] CloudDump ${HOST}" "${MAILTO}"
fi

log "Startup email sent."


# Determines if a given timestamp matches a cron schedule pattern
#
# This function evaluates whether a specific point in time satisfies a cron pattern.
# It supports common cron syntax: wildcards (*), exact matches (5), and step values (*/15).
# Ranges (1-5) and lists (1,3,5) are intentionally not supported as they are not used
# in any documented configurations for this application.
#
# Arguments:
#   $1 - cron_pattern: Standard 5-field cron pattern (minute hour day month day-of-week)
#   $2 - unix_timestamp: Unix timestamp (seconds since epoch) to evaluate
#
# Returns:
#   0 (success) if the timestamp matches the cron pattern
#   1 (failure) if the timestamp does not match
#
# Example:
#   matches_cron_pattern "*/5 * * * *" "1704067200"  # Returns 0 if minute is divisible by 5
#
matches_cron_pattern() {
  local cron_pattern="$1"
  local unix_timestamp="$2"
  
  # Extract time components from the Unix timestamp
  local minute hour day_of_month month day_of_week
  minute=$(date -d "@${unix_timestamp}" '+%-M')
  hour=$(date -d "@${unix_timestamp}" '+%-H')
  day_of_month=$(date -d "@${unix_timestamp}" '+%-d')
  month=$(date -d "@${unix_timestamp}" '+%-m')
  day_of_week=$(date -d "@${unix_timestamp}" '+%u')  # 1-7, Monday is 1
  
  # Convert Sunday from 7 to 0 to match standard cron behavior (0=Sunday, 6=Saturday)
  [ "${day_of_week}" = "7" ] && day_of_week="0"
  
  # Parse the cron pattern into individual field values
  read -r pattern_minute pattern_hour pattern_day pattern_month pattern_dow <<< "${cron_pattern}"
  
  # Evaluates if a single cron field matches the corresponding time value
  # Handles wildcards (*), step values (*/N), and exact numeric matches
  matches_field_pattern() {
    local pattern_field="$1"
    local time_value="$2"
    
    # Wildcard matches any value
    [ "${pattern_field}" = "*" ] && return 0
    
    # Step values: */N means "every N units" (e.g., */5 for every 5 minutes)
    if echo "${pattern_field}" | grep -q '^\*/[0-9]\+$'; then
      local step_value
      step_value=$(echo "${pattern_field}" | sed 's|^\*/||')
      [ $((time_value % step_value)) -eq 0 ] && return 0
      return 1
    fi
    
    # Exact match: pattern must equal the time value
    [ "${pattern_field}" = "${time_value}" ] && return 0
    return 1
  }
  
  # Evaluate all five cron fields; all must match for the pattern to match
  matches_field_pattern "${pattern_minute}" "${minute}" || return 1
  matches_field_pattern "${pattern_hour}" "${hour}" || return 1
  matches_field_pattern "${pattern_day}" "${day_of_month}" || return 1
  matches_field_pattern "${pattern_month}" "${month}" || return 1
  matches_field_pattern "${pattern_dow}" "${day_of_week}" || return 1
  
  return 0
}

# Determines if a job should execute based on its schedule and last run time
#
# This function implements "catch-up execution" by checking if the job's cron pattern
# matched at any point between the last run and the current time. This ensures that
# jobs scheduled during periods when other jobs were running will still execute.
#
# The function looks backward in time, minute by minute, from the last execution
# to the current minute, checking if any of those minutes match the cron pattern.
#
# Arguments:
#   $1 - cron_pattern: The job's schedule in standard cron format (minute hour day month dow)
#   $2 - last_run_unix_timestamp: Unix timestamp of when the job last completed (0 if never run)
#
# Returns:
#   0 (success) if the job should run now
#   1 (failure) if the job should not run yet
#
# Example:
#   determines_job_execution_needed "*/5 * * * *" "1704067200"
#
determines_job_execution_needed() {
  local cron_pattern="$1"
  local last_run_unix_timestamp="$2"
  local current_unix_timestamp
  current_unix_timestamp=$(date +%s)
  
  # For first-time execution, check if current time matches the pattern
  if [ "${last_run_unix_timestamp}" = "0" ]; then
    matches_cron_pattern "${cron_pattern}" "${current_unix_timestamp}"
    return $?
  fi
  
  # Calculate the start of the minute for both last run and current time
  # This ensures we check full minutes, not partial seconds
  local last_run_minute_boundary current_minute_boundary
  last_run_minute_boundary=$(date -d "@${last_run_unix_timestamp}" '+%Y-%m-%d %H:%M:00')
  last_run_minute_boundary=$(date -d "${last_run_minute_boundary}" +%s)
  current_minute_boundary=$(date -d "$(date -d "@${current_unix_timestamp}" '+%Y-%m-%d %H:%M:00')" +%s)
  
  # Iterate through each minute since last run, checking if any match the cron pattern
  # Start from the minute after the last run to avoid double-execution
  local timestamp_to_check=$((last_run_minute_boundary + 60))
  
  while [ "${timestamp_to_check}" -le "${current_minute_boundary}" ]; do
    if matches_cron_pattern "${cron_pattern}" "${timestamp_to_check}"; then
      return 0  # Found a match, job should run
    fi
    timestamp_to_check=$((timestamp_to_check + 60))
  done
  
  return 1  # No matches found, job should not run yet
}


# Main loop - check every minute and run jobs sequentially
log "Starting main loop..."

# Track last run time for each job (initialize to 0)
declare -A last_run_times

while true; do
  
  # Check each job
  for ((i = 0; i < jobs; i++)); do
    
    if ! jobid=$(jq -r ".jobs[${i}].id" "${CONFIGFILE}" | sed 's/^null$//g') || [ "${jobid}" = "" ]; then
      continue
    fi
    
    if ! type=$(jq -r ".jobs[${i}].type" "${CONFIGFILE}" | sed 's/^null$//g') || [ "${type}" = "" ]; then
      continue
    fi
    
    if ! crontab=$(jq -r ".jobs[${i}].crontab" "${CONFIGFILE}" | sed 's/^null$//g') || [ "${crontab}" = "" ]; then
      continue
    fi
    
    jobdebug=$(jq -r ".jobs[${i}].debug" "${CONFIGFILE}")
    
    # Initialize last run time if not set
    if [ -z "${last_run_times[${jobid}]}" ]; then
      last_run_times[${jobid}]="0"
    fi
    
    # Evaluate if the job's schedule indicates it should execute now
    # This implements catch-up execution for jobs that should have run while other jobs were executing
    if determines_job_execution_needed "${crontab}" "${last_run_times[${jobid}]}"; then
      
      log "Running job ${jobid} (type: ${type})"
        
        # Create log file
        RANDOM=$$
        LOGFILE="/tmp/vnclouddump-${jobid}-${RANDOM}.log"
        
        time_start=$(date +%s)
        time_start_timestamp=$(get_timestamp)
        
        log "Job ${jobid} starting at ${time_start_timestamp}" >> "${LOGFILE}"
        
        # Run the script based on type
        result=0
        
        if [ "${type}" = "s3bucket" ]; then
          execute_s3bucket_job "${i}" "${jobid}" "${jobdebug}" "${LOGFILE}"
          result=$?
        elif [ "${type}" = "azstorage" ]; then
          execute_azstorage_job "${i}" "${jobid}" "${jobdebug}" "${LOGFILE}"
          result=$?
        elif [ "${type}" = "pgsql" ]; then
          execute_pgsql_job "${i}" "${jobid}" "${jobdebug}" "${LOGFILE}"
          result=$?
        else
          # Unknown type - should not happen
          log "Error: Unknown job type ${type} for job ${jobid}." >> "${LOGFILE}"
          result=1
        fi
        
        time_end=$(date +%s)
        
        log "Job ${jobid} finished at $(get_timestamp)" >> "${LOGFILE}"
        
        if [ ${result} -eq 0 ]; then
          log "Job ${jobid} completed successfully"
        else
          log "Job ${jobid} completed with errors (exit code: ${result})"
        fi
        
        # Get configuration for email
        configuration=$(retrieves_job_configuration_by_id "${jobid}")
        
        # Send email report
        sends_job_completion_email "${jobid}" "${script}" "${result}" "${time_start}" "${time_end}" "${time_start_timestamp}" "${LOGFILE}" "${configuration}"
          
        # Clean up log file
        rm -f "${LOGFILE}"
        
        # Update last run time for this job
        last_run_times[${jobid}]=$(date +%s)
        
    fi
    
  done
  
  # Sleep until the next minute boundary
  current_second=$(date '+%-S')
  sleep_seconds=$((60 - current_second))
  # Ensure we always sleep at least 1 second
  if [ "${sleep_seconds}" -le 0 ]; then
    sleep_seconds=1
  fi
  sleep ${sleep_seconds}
  
done
