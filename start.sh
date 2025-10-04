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

timestamp() {

  date '+%Y-%m-%d %H:%M:%S'

}

log() {

  echo "[$(timestamp)] $*"

}

error() {

  error="$*"
  echo "[$(timestamp)] ERROR: ${error}" >&2

}

json_array_to_strlist() {

  local i
  local output
  count=$(jq -r "${1} | length" "${CONFIGFILE}")
  for ((i = 0; i < count; i++)); do
    local value
    value=$(jq -r "${1}[${i}]" "${CONFIGFILE}" | sed 's/^null$//g')
    if [ $? -ne 0 ] || [ "$value" = "" ] ; then
      continue
    fi
    if [ "${output}" = "" ]; then
      output="${value}"
    else
      output="${output} ${value}"
    fi
  done

  echo "${output}"

}

# Function to redact sensitive information from text
redact_sensitive() {
  local text="$1"
  # Redact passwords, keys, tokens, and SAS tokens
  text=$(echo "${text}" | sed 's/\(password\|pass\|key\|token\|secret\)[[:space:]]*[:=][[:space:]]*[^[:space:]]*/\1: [REDACTED]/gi')
  text=$(echo "${text}" | sed 's/\?[^?]*\(sig\|se\|st\|sp\)=[^&?]*/\?[REDACTED]/g')
  echo "${text}"
}

# Signal handler for graceful shutdown
shutdown_handler() {
  log "Received shutdown signal, exiting gracefully..."
  exit 0
}

# Helper function to format job configuration from JSON
format_job_config() {
  local job_idx="$1"
  
  # Get the job object and format it for display, removing sensitive fields
  jq -r ".jobs[${job_idx}] | 
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

# Function to send email with job results
send_job_email() {
  local jobid="$1"
  local script="$2"
  local result="$3"
  local time_start="$4"
  local time_end="$5"
  local time_start_timestamp="$6"
  local logfile="$7"
  local configuration="$8"
  
  local result_text
  if [ ${result} -eq 0 ]; then
    result_text="Success"
  else
    result_text="Failure"
  fi
  
  local scriptfilename
  echo "${script}" | grep '\/' >/dev/null 2>&1
  if [ $? -eq 0 ]; then
    scriptfilename=$(echo "${script}" | sed 's/.*\///g')
  else
    scriptfilename="${script}"
  fi
  
  log "Sending e-mail to ${MAILTO} from ${MAILFROM} for job ${jobid}."
  
  # Check mail command type
  local mailattachopt
  if [ "${MAIL}" = "mail" ]; then
    "${MAIL}" -V >/dev/null 2>&1
    if [ $? -eq 0 ]; then
      "${MAIL}" -V | grep "^mail (GNU Mailutils)" >/dev/null 2>&1
      if [ $? -eq 0 ]; then
        mailattachopt="-A"
      else
        mailattachopt="-a"
      fi
    else
      mailattachopt="-A"
    fi
  elif [ "${MAIL}" = "mutt" ]; then
    mailattachopt="-a"
  else
    log "Unknown mail command: ${MAIL}"
    return 1
  fi
  
  local attachments="${mailattachopt} ${logfile}"
  
  # Check for azcopy log files
  if [ -f "${logfile}" ]; then
    azcopy_logfiles=$(grep '^Log file is located at: .*\.log$' ${logfile} | sed -e 's/Log file is located at: \(.*\)/\1/' | sed 's/\r$//' | tr '\n' ' ' | sed 's/ $//g')
    if ! [ "${azcopy_logfiles}" = "" ]; then
      for azcopy_logfile in ${azcopy_logfiles}; do
        if [ ! "${azcopy_logfile}" = "" ] && [ -f "${azcopy_logfile}" ]; then
          attachments="${attachments} ${mailattachopt} ${azcopy_logfile}"
        fi
      done
    fi
  fi
  
  attachments="${attachments} --"
  
  local message
  message="CloudDump ${HOST}

JOB REPORT (${result_text})

Script: ${scriptfilename}
ID: ${jobid}
Started: ${time_start_timestamp}
Completed: $(timestamp)
Time elapsed: $(((time_end - time_start)/60)) minutes $(((time_end - time_start)%60)) seconds

CONFIGURATION

${configuration}

For more information consult the attached logs.

Vendanor CloudDump v${VERSION}
"
  
  if [ "${MAIL}" = "mutt" ]; then
    echo "${message}" | EMAIL="${MAILFROM} <${MAILFROM}>" ${MAIL} -s "[${result_text}] CloudDump ${HOST}: ${jobid}" ${attachments} "${MAILTO}"
  else
    echo "${message}" | ${MAIL} -r "${MAILFROM} <${MAILFROM}>" -s "[${result_text}] CloudDump ${HOST}: ${jobid}" ${attachments} "${MAILTO}"
  fi
}

# Function to get job configuration for email
get_job_configuration() {
  local jobid="$1"
  
  # Find the job index for this job ID
  local jobs job_idx
  jobs=$(jq -r ".jobs | length" "${CONFIGFILE}")
  if [ "${jobs}" = "" ] || [ -z "${jobs}" ] || ! [ "${jobs}" -eq "${jobs}" ] 2>/dev/null; then
    echo ""
    return 1
  fi

  job_idx=
  for ((i = 0; i < jobs; i++)); do
    local jobid_current
    jobid_current=$(jq -r ".jobs[${i}].id" "${CONFIGFILE}" | sed 's/^null$//g')
    if [ $? -ne 0 ] || [ "${jobid_current}" = "" ]; then
      continue
    fi
    if [ "${jobid_current}" = "${jobid}" ]; then
      job_idx="${i}"
      break
    fi
  done

  if [ "${job_idx}" = "" ]; then
    echo ""
    return 1
  fi

  # Use jq to format the configuration directly from JSON
  format_job_config "${job_idx}"
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
    
    # Pass the databases and databases_excluded configuration as JSON to the dump script
    databases_json=$(jq -c ".jobs[${job_idx}].servers[${server_idx}].databases // []" "${CONFIGFILE}")
    databases_excluded_json=$(jq -c ".jobs[${job_idx}].servers[${server_idx}].databases_excluded // []" "${CONFIGFILE}")
    
    if [ "${jobdebug}" = "true" ]; then
      /bin/bash -x dump_pgsql.sh "${PGHOST}" "${PGPORT}" "${PGUSERNAME}" "${PGPASSWORD}" "${backuppath}" "${filenamedate}" "${compress}" "${databases_json}" "${databases_excluded_json}" >> "${logfile}" 2>&1
      server_result=$?
    else
      /bin/bash dump_pgsql.sh "${PGHOST}" "${PGPORT}" "${PGUSERNAME}" "${PGPASSWORD}" "${backuppath}" "${filenamedate}" "${compress}" "${databases_json}" "${databases_excluded_json}" >> "${logfile}" 2>&1
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
trap 'shutdown_handler' SIGTERM SIGINT


# Check commands

cmds="which grep sed cut cp chmod mkdir bc jq mail mutt postconf postmap ssh sshfs smbnetfs"
cmds_missing=
for cmd in ${cmds}
do
  which "${cmd}" >/dev/null 2>&1
  if [ $? -eq 0 ] ; then
    continue
  fi
  if [ "${cmds_missing}" = "" ]; then
    cmds_missing="${cmd}"
  else
    cmds_missing="${cmds_missing} ${cmd}"
  fi
done

if ! [ "${cmds_missing}" = "" ]; then
  error "Missing \"${cmds_missing}\" commands."
  exit 1
fi


# Read settings

if [ ! -f "${CONFIGFILE}" ]; then
  error "Missing Json configuration file ${CONFIGFILE}."
  exit 1
fi

if [ ! -r "${CONFIGFILE}" ]; then
  error "Can't read Json configuration file ${CONFIGFILE}."
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
touch /etc/postfix/sasl_passwd || exit 1
touch /etc/Muttrc || exit 1

if ! [ "${SMTPSERVER}" = "" ] && ! [ "${SMTPPORT}" = "" ]; then
  log "SMTP server: $SMTPSERVER"
  log "SMTP port: $SMTPPORT"
  log "SMTP username: $SMTPUSER"
  if [ "$SMTPUSER" = "" ] && [ "$SMTPPASS" = "" ]; then
    SMTPURL="smtps://${SMTPSERVER}:${SMTPPORT}"
  else
    SMTPURL="smtps://${SMTPUSER}:${SMTPPASS}@${SMTPSERVER}:${SMTPPORT}"
    grep "^\[${SMTPSERVER}\]:${SMTPPORT} ${SMTPUSER}:${SMTPPASS}$" /etc/postfix/sasl_passwd >/dev/null
    if [ $? -ne 0 ]; then
      echo "[${SMTPSERVER}]:${SMTPPORT} ${SMTPUSER}:${SMTPPASS}" >> /etc/postfix/sasl_passwd || exit 1
    fi
  fi
  grep "^set smtp_url=\"${SMTPURL}\"$" /etc/Muttrc >/dev/null
  if [ $? -ne 0 ]; then
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
    path=$(jq -r ".settings.mount[${i}].path" "${CONFIGFILE}" | sed 's/^null$//g' | sed 's/\\/\//g')
    if [ $? -ne 0 ] || [ "${path}" = "" ]; then
      continue
    fi
    mountpoint=$(jq -r ".settings.mount[${i}].mountpoint" "${CONFIGFILE}" | sed 's/^null$//g')
    if [ $? -ne 0 ] || [ "${mountpoint}" = "" ]; then
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

    echo "${path}" | grep ':' >/dev/null 2>&1
    if [ $? -eq 0 ]; then # SSH
      if [ ! "${privkey}" = "" ]; then
        mkdir -p "${HOME}/.ssh" || exit 1
        echo "${privkey}" >"${HOME}/.ssh/id_rsa" || exit 1
        chmod 600 "${HOME}/.ssh/id_rsa" || exit 1
      fi
      echo "${path}" | grep '@' >/dev/null 2>&1
      if [ $? -ne 0 ] && ! [ "${username}" = "" ]; then
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
    echo "${path}" | grep '^\/\/' >/dev/null 2>&1
    if [ $? -eq 0 ]; then # SMB
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
    error "Invalid path ${path} for mountpoint ${mountpoint}."
    error "Syntax is \"user@host:/path\" for SSH, or \"//host/path\" for SMB."
    exit 1
  done
fi


# Read and validate jobs configuration

jobs=$(jq -r ".jobs | length" "${CONFIGFILE}")
if [ "${jobs}" = "" ] || [ -z "${jobs}" ] || ! [ "${jobs}" -eq "${jobs}" ] 2>/dev/null; then
  error "Can't read jobs from Json configuration."
  exit 1
fi

if [ "${jobs}" -eq 0 ]; then
  error "No jobs in Json configuration."
  exit 1
fi

# Build job summary for startup email
jobs_summary=""
for ((i = 0; i < jobs; i++)); do

  jobid=$(jq -r ".jobs[${i}].id" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ] || [ "${jobid}" = "" ]; then
    error "Missing job ID for job index ${i}."
    continue
  fi

  type=$(jq -r ".jobs[${i}].type" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ] || [ "${type}" = "" ]; then
    error "Missing type for job ID ${jobid}."
    continue
  fi
  
  script="dump_${type}.sh"

  crontab=$(jq -r ".jobs[${i}].crontab" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ] || [ "${crontab}" = "" ]; then
    error "Missing crontab for job ID ${jobid}."
    continue
  fi

  jobdebug=$(jq -r ".jobs[${i}].debug" "${CONFIGFILE}")

  echo "${script}" | grep '^\/' >/dev/null 2>&1
  if [ $? -eq 0 ]; then
    scriptfile="${script}"
  else
    scriptfile=$(which "${script}" 2>/dev/null)
    if [ "${scriptfile}" = "" ]; then
      error "Missing scriptfile ${script}."
      exit 1
    fi
  fi

  if ! [ -f "${scriptfile}" ]; then
    error "Missing scriptfile ${scriptfile}."
    exit 1
  fi

  if ! [ -x "${scriptfile}" ]; then
    error "Scriptfile ${scriptfile} not executable."
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
startup_config=$(redact_sensitive "${startup_config}")
jobs_summary=$(redact_sensitive "${jobs_summary}")

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


# Helper function to check if a timestamp matches a cron pattern
check_cron_match_timestamp() {
  local cron_pattern="$1"
  local timestamp="$2"
  local check_min
  local check_hour
  local check_day
  local check_month
  local check_dow
  check_min=$(date -d "@${timestamp}" '+%-M')
  check_hour=$(date -d "@${timestamp}" '+%-H')
  check_day=$(date -d "@${timestamp}" '+%-d')
  check_month=$(date -d "@${timestamp}" '+%-m')
  check_dow=$(date -d "@${timestamp}" '+%u')  # 1-7, Monday is 1
  
  # Convert Sunday from 7 to 0 for cron compatibility
  if [ "${check_dow}" = "7" ]; then
    check_dow="0"
  fi
  
  # Parse cron pattern (minute hour day month dow)
  read -r cron_min cron_hour cron_day cron_month cron_dow <<< "${cron_pattern}"
  
  # Check each field
  check_field() {
    local field="$1"
    local value="$2"
    
    # Handle wildcard
    if [ "${field}" = "*" ]; then
      return 0
    fi
    
    # Handle step values (e.g., */5)
    if echo "${field}" | grep -q '^\*/[0-9]\+$'; then
      local step
      step=$(echo "${field}" | sed 's|^\*/||')
      if [ $((value % step)) -eq 0 ]; then
        return 0
      fi
      return 1
    fi
    
    # Handle ranges (e.g., 1-5)
    if echo "${field}" | grep -q '^[0-9]\+-[0-9]\+$'; then
      local start
      local end
      start=$(echo "${field}" | cut -d'-' -f1)
      end=$(echo "${field}" | cut -d'-' -f2)
      if [ "${value}" -ge "${start}" ] && [ "${value}" -le "${end}" ]; then
        return 0
      fi
      return 1
    fi
    
    # Handle lists (e.g., 1,3,5)
    if echo "${field}" | grep -q ','; then
      local IFS=','
      for item in ${field}; do
        if [ "${item}" = "${value}" ]; then
          return 0
        fi
      done
      return 1
    fi
    
    # Handle exact match
    if [ "${field}" = "${value}" ]; then
      return 0
    fi
    
    return 1
  }
  
  if check_field "${cron_min}" "${check_min}" && \
     check_field "${cron_hour}" "${check_hour}" && \
     check_field "${cron_day}" "${check_day}" && \
     check_field "${cron_month}" "${check_month}" && \
     check_field "${cron_dow}" "${check_dow}"; then
    return 0
  fi
  
  return 1
}

# Helper function to check if job should run by looking backward in time
# Returns 0 (true) if the cron pattern matched at any point since last run
should_job_run() {
  local cron_pattern="$1"
  local last_run_timestamp="$2"
  local current_timestamp
  current_timestamp=$(date +%s)
  
  # If never run before, check if current time matches
  if [ "${last_run_timestamp}" = "0" ]; then
    check_cron_match_timestamp "${cron_pattern}" "${current_timestamp}"
    return $?
  fi
  
  # Round last_run_timestamp and current_timestamp to the start of their respective minutes
  local last_run_minute_start
  local current_minute_start
  last_run_minute_start=$(date -d "@${last_run_timestamp}" '+%Y-%m-%d %H:%M:00')
  last_run_minute_start=$(date -d "${last_run_minute_start}" +%s)
  current_minute_start=$(date -d "$(date -d "@${current_timestamp}" '+%Y-%m-%d %H:%M:00')" +%s)
  
  # Check each minute from the minute after last run to current minute
  local check_timestamp=$((last_run_minute_start + 60))
  
  while [ ${check_timestamp} -le ${current_minute_start} ]; do
    if check_cron_match_timestamp "${cron_pattern}" "${check_timestamp}"; then
      return 0
    fi
    check_timestamp=$((check_timestamp + 60))
  done
  
  return 1
}


# Main loop - check every minute and run jobs sequentially
log "Starting main loop..."

# Track last run time for each job (initialize to 0)
declare -A last_run_times

while true; do
  
  current_minute=$(date '+%Y-%m-%d %H:%M')
  
  # Check each job
  for ((i = 0; i < jobs; i++)); do
    
    jobid=$(jq -r ".jobs[${i}].id" "${CONFIGFILE}" | sed 's/^null$//g')
    if [ $? -ne 0 ] || [ "${jobid}" = "" ]; then
      continue
    fi
    
    type=$(jq -r ".jobs[${i}].type" "${CONFIGFILE}" | sed 's/^null$//g')
    if [ $? -ne 0 ] || [ "${type}" = "" ]; then
      continue
    fi
    
    crontab=$(jq -r ".jobs[${i}].crontab" "${CONFIGFILE}" | sed 's/^null$//g')
    if [ $? -ne 0 ] || [ "${crontab}" = "" ]; then
      continue
    fi
    
    jobdebug=$(jq -r ".jobs[${i}].debug" "${CONFIGFILE}")
    
    # Initialize last run time if not set
    if [ -z "${last_run_times[${jobid}]}" ]; then
      last_run_times[${jobid}]="0"
    fi
    
    # Check if job should run by looking backward from last run time
    if should_job_run "${crontab}" "${last_run_times[${jobid}]}"; then
      
      log "Running job ${jobid} (type: ${type})"
        
        # Create log file
        RANDOM=$$
        LOGFILE="/tmp/vnclouddump-${jobid}-${RANDOM}.log"
        
        time_start=$(date +%s)
        time_start_timestamp=$(timestamp)
        
        log "Job ${jobid} starting at ${time_start_timestamp}" >> "${LOGFILE}"
        
        # Run the script based on type
        result=0
        
        if [ "${type}" = "s3bucket" ]; then
          # For s3bucket, parse config and call script for each bucket
          bucket_count=$(jq -r ".jobs[${i}].buckets | length" "${CONFIGFILE}")
          if [ "${bucket_count}" = "" ] || [ -z "${bucket_count}" ] || ! [ "${bucket_count}" -eq "${bucket_count}" ] 2>/dev/null; then
            log "Error: Can't read buckets from Json configuration for job ${jobid}." >> "${LOGFILE}"
            result=1
          elif [ "${bucket_count}" -eq 0 ]; then
            log "Error: No buckets for ${jobid} in Json configuration." >> "${LOGFILE}"
            result=1
          else
            for ((bucket_idx = 0; bucket_idx < bucket_count; bucket_idx++)); do
              source=$(jq -r ".jobs[${i}].buckets[${bucket_idx}].source" "${CONFIGFILE}" | sed 's/^null$//g')
              destination=$(jq -r ".jobs[${i}].buckets[${bucket_idx}].destination" "${CONFIGFILE}" | sed 's/^null$//g')
              delete_destination=$(jq -r ".jobs[${i}].buckets[${bucket_idx}].delete_destination" "${CONFIGFILE}" | sed 's/^null$//g')
              aws_access_key_id=$(jq -r ".jobs[${i}].buckets[${bucket_idx}].aws_access_key_id" "${CONFIGFILE}" | sed 's/^null$//g')
              aws_secret_access_key=$(jq -r ".jobs[${i}].buckets[${bucket_idx}].aws_secret_access_key" "${CONFIGFILE}" | sed 's/^null$//g')
              aws_region=$(jq -r ".jobs[${i}].buckets[${bucket_idx}].aws_region" "${CONFIGFILE}" | sed 's/^null$//g')
              endpoint_url=$(jq -r ".jobs[${i}].buckets[${bucket_idx}].endpoint_url" "${CONFIGFILE}" | sed 's/^null$//g')
              
              if [ "${jobdebug}" = "true" ]; then
                /bin/bash -x "${scriptfile}" -s "${source}" -d "${destination}" -m "${delete_destination}" -a "${aws_access_key_id}" -k "${aws_secret_access_key}" -r "${aws_region}" -e "${endpoint_url}" >> "${LOGFILE}" 2>&1
                bucket_result=$?
              else
                /bin/bash "${scriptfile}" -s "${source}" -d "${destination}" -m "${delete_destination}" -a "${aws_access_key_id}" -k "${aws_secret_access_key}" -r "${aws_region}" -e "${endpoint_url}" >> "${LOGFILE}" 2>&1
                bucket_result=$?
              fi
              
              if [ ${bucket_result} -ne 0 ]; then
                result=${bucket_result}
              fi
            done
          fi
        elif [ "${type}" = "azstorage" ]; then
          # For azstorage, parse config and call script for each blobstorage
          bs_count=$(jq -r ".jobs[${i}].blobstorages | length" "${CONFIGFILE}")
          if [ "${bs_count}" = "" ] || [ -z "${bs_count}" ] || ! [ "${bs_count}" -eq "${bs_count}" ] 2>/dev/null; then
            log "Error: Can't read blobstorages from Json configuration for job ${jobid}." >> "${LOGFILE}"
            result=1
          elif [ "${bs_count}" -eq 0 ]; then
            log "Error: No blobstorages for ${jobid} in Json configuration." >> "${LOGFILE}"
            result=1
          else
            for ((bs_idx = 0; bs_idx < bs_count; bs_idx++)); do
              source=$(jq -r ".jobs[${i}].blobstorages[${bs_idx}].source" "${CONFIGFILE}" | sed 's/^null$//g')
              destination=$(jq -r ".jobs[${i}].blobstorages[${bs_idx}].destination" "${CONFIGFILE}" | sed 's/^null$//g')
              delete_destination=$(jq -r ".jobs[${i}].blobstorages[${bs_idx}].delete_destination" "${CONFIGFILE}" | sed 's/^null$//g')
              
              if [ "${jobdebug}" = "true" ]; then
                /bin/bash -x "${scriptfile}" -s "${source}" -d "${destination}" -m "${delete_destination}" >> "${LOGFILE}" 2>&1
                bs_result=$?
              else
                /bin/bash "${scriptfile}" -s "${source}" -d "${destination}" -m "${delete_destination}" >> "${LOGFILE}" 2>&1
                bs_result=$?
              fi
              
              if [ ${bs_result} -ne 0 ]; then
                result=${bs_result}
              fi
            done
          fi
        else
          # For pgsql type, parse config and call script for each database
          if [ "${type}" = "pgsql" ]; then
            server_count=$(jq -r ".jobs[${i}].servers | length" "${CONFIGFILE}")
            if [ "${server_count}" = "" ] || [ -z "${server_count}" ] || ! [ "${server_count}" -eq "${server_count}" ] 2>/dev/null; then
              log "Error: Can't read servers from Json configuration for job ${jobid}." >> "${LOGFILE}"
              result=1
            elif [ "${server_count}" -eq 0 ]; then
              log "Error: No servers for ${jobid} in Json configuration." >> "${LOGFILE}"
              result=1
            else
              for ((server_idx = 0; server_idx < server_count; server_idx++)); do
                PGHOST=$(jq -r ".jobs[${i}].servers[${server_idx}].host" "${CONFIGFILE}" | sed 's/^null$//g')
                PGPORT=$(jq -r ".jobs[${i}].servers[${server_idx}].port" "${CONFIGFILE}" | sed 's/^null$//g')
                PGUSERNAME=$(jq -r ".jobs[${i}].servers[${server_idx}].user" "${CONFIGFILE}" | sed 's/^null$//g')
                PGPASSWORD=$(jq -r ".jobs[${i}].servers[${server_idx}].pass" "${CONFIGFILE}" | sed 's/^null$//g')
                backuppath=$(jq -r ".jobs[${i}].servers[${server_idx}].backuppath" "${CONFIGFILE}" | sed 's/^null$//g')
                filenamedate=$(jq -r ".jobs[${i}].servers[${server_idx}].filenamedate" "${CONFIGFILE}" | sed 's/^null$//g')
                compress=$(jq -r ".jobs[${i}].servers[${server_idx}].compress" "${CONFIGFILE}" | sed 's/^null$//g')
                
                # Get list of databases with explicit configuration
                databases_configured=$(jq -r ".jobs[${i}].servers[${server_idx}].databases[] | keys[]" "${CONFIGFILE}" 2>/dev/null | tr '\n' ' ')
                databases_excluded_list=$(jq -r ".jobs[${i}].servers[${server_idx}].databases_excluded[]" "${CONFIGFILE}" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
                
                # Get all databases from server
                databases_all=$(PGPASSWORD=${PGPASSWORD} psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSERNAME}" -l 2>/dev/null | grep '|' | sed 's/ //g' | grep -v '^Name|' | grep -v '^||' | cut -d '|' -f 1 | sed -z 's/\n/ /g;s/ $/\n/')
                
                # Determine which databases to backup
                databases_backup=""
                if [ ! "${databases_configured}" = "" ]; then
                  # Use only explicitly configured databases
                  databases_backup="${databases_configured}"
                else
                  # Use all databases, excluding those in databases_excluded
                  for database in ${databases_all}
                  do
                    if echo ",${databases_excluded_list}," | grep -q ",${database},"; then
                      continue
                    fi
                    databases_backup="${databases_backup} ${database}"
                  done
                fi
                
                # Backup each database
                for database in ${databases_backup}
                do
                  # Get table configuration for this database
                  tables_included=""
                  tables_excluded=""
                  
                  db_count=$(jq -r ".jobs[${i}].servers[${server_idx}].databases | length" "${CONFIGFILE}" 2>/dev/null)
                  for ((db_idx = 0; db_idx < db_count; db_idx++)); do
                    jq_output=$(jq -r ".jobs[${i}].servers[${server_idx}].databases[${db_idx}][\"${database}\"] | length" "${CONFIGFILE}" 2>/dev/null | sed 's/^null$//g')
                    if [ "${jq_output}" = "" ] || [ -z "${jq_output}" ] || ! [ "${jq_output}" -eq "${jq_output}" ] || [ "${jq_output}" -eq 0 ] 2>/dev/null; then
                      continue
                    fi
                    
                    tables_excluded=$(jq -r ".jobs[${i}].servers[${server_idx}].databases[${db_idx}][\"${database}\"].tables_excluded[]" "${CONFIGFILE}" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
                    tables_included=$(jq -r ".jobs[${i}].servers[${server_idx}].databases[${db_idx}][\"${database}\"].tables_included[]" "${CONFIGFILE}" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
                    break
                  done
                  
                  if [ "${jobdebug}" = "true" ]; then
                    /bin/bash -x "${scriptfile}" -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSERNAME}" -P "${PGPASSWORD}" -d "${database}" -b "${backuppath}" -f "${filenamedate}" -z "${compress}" -i "${tables_included}" -x "${tables_excluded}" >> "${LOGFILE}" 2>&1
                    db_result=$?
                  else
                    /bin/bash "${scriptfile}" -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSERNAME}" -P "${PGPASSWORD}" -d "${database}" -b "${backuppath}" -f "${filenamedate}" -z "${compress}" -i "${tables_included}" -x "${tables_excluded}" >> "${LOGFILE}" 2>&1
                    db_result=$?
                  fi
                  
                  if [ ${db_result} -ne 0 ]; then
                    result=${db_result}
                  fi
                done
              done
            fi
          else
            # Unknown type - should not happen
            log "Error: Unknown job type ${type} for job ${jobid}." >> "${LOGFILE}"
            result=1
          fi
        fi
        
        time_end=$(date +%s)
        
        log "Job ${jobid} finished at $(timestamp)" >> "${LOGFILE}"
        
        if [ ${result} -eq 0 ]; then
          log "Job ${jobid} completed successfully"
        else
          log "Job ${jobid} completed with errors (exit code: ${result})"
        fi
        
        # Get configuration for email
        configuration=$(get_job_configuration "${jobid}")
        
        # Send email report
        send_job_email "${jobid}" "${script}" "${result}" "${time_start}" "${time_end}" "${time_start_timestamp}" "${LOGFILE}" "${configuration}"
          
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
