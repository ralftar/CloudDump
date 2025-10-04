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

# Signal handler for graceful shutdown
shutdown_handler() {
  log "Received shutdown signal, exiting gracefully..."
  exit 0
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
  local script="$2"
  
  # Find the job index for this job ID
  jobs=$(jq -r ".jobs | length" "${CONFIGFILE}")
  if [ "${jobs}" = "" ] || [ -z "${jobs}" ] || ! [ "${jobs}" -eq "${jobs}" ] 2>/dev/null; then
    echo ""
    return 1
  fi

  job_idx=
  for ((i = 0; i < jobs; i++)); do
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

  crontab=$(jq -r ".jobs[${job_idx}].crontab" "${CONFIGFILE}")
  debug=$(jq -r ".jobs[${job_idx}].debug" "${CONFIGFILE}")
  
  local configuration=""

  if [ "${script}" = "dump_azstorage.sh" ]; then

    bs_count=$(jq -r ".jobs[${job_idx}].blobstorages | length" "${CONFIGFILE}")
    if [ "${bs_count}" = "" ] || [ -z "${bs_count}" ] || ! [ "${bs_count}" -eq "${bs_count}" ] 2>/dev/null; then
      bs_count=0
    fi

    local blobstorages=""
    for ((bs_idx = 0; bs_idx < bs_count; bs_idx++)); do

      source=$(jq -r ".jobs[${job_idx}].blobstorages[${bs_idx}].source" "${CONFIGFILE}" | sed 's/^null$//g')
      destination=$(jq -r ".jobs[${job_idx}].blobstorages[${bs_idx}].destination" "${CONFIGFILE}" | sed 's/^null$//g')
      delete_destination=$(jq -r ".jobs[${job_idx}].blobstorages[${bs_idx}].delete_destination" "${CONFIGFILE}" | sed 's/^null$//g')

      if [ "${delete_destination}" = "" ]; then
        delete_destination="false"
      fi

      source_stripped=$(echo "${source}" | cut -d '?' -f 1)

      blobstorage="Source: ${source_stripped}
Destination: ${destination}   
Delete destination: ${delete_destination}   "

      if [ "${blobstorages}" = "" ]; then
        blobstorages="${blobstorage}"
      else
        blobstorages="${blobstorages}
${blobstorage}"
      fi

    done

    configuration="Schedule: ${crontab}
Debug: ${debug}
${blobstorages}"

  elif [ "${script}" = "dump_pgsql.sh" ]; then

    server_count=$(jq -r ".jobs[${job_idx}].servers | length" "${CONFIGFILE}")
    if [ "${server_count}" = "" ] || [ -z "${server_count}" ] || ! [ "${server_count}" -eq "${server_count}" ] 2>/dev/null; then
      server_count=0
    fi

    local entry_servers=""
    for ((server_idx = 0; server_idx < server_count; server_idx++)); do

      PGHOST=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].host" "${CONFIGFILE}" | sed 's/^null$//g')
      PGPORT=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].port" "${CONFIGFILE}" | sed 's/^null$//g')
      PGUSERNAME=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].user" "${CONFIGFILE}" | sed 's/^null$//g')
      backuppath=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].backuppath" "${CONFIGFILE}" | sed 's/^null$//g')
      filenamedate=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].filenamedate" "${CONFIGFILE}" | sed 's/^null$//g')
      compress=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].compress" "${CONFIGFILE}" | sed 's/^null$//g')

      databases=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].databases[] | keys[]" "${CONFIGFILE}" 2>/dev/null | tr '\n' ' ')
      databases_excluded=$(json_array_to_strlist ".jobs[${job_idx}].servers[${server_idx}].databases_excluded")

      local database_configuration=""
      local databases_configuration=""

      for database in ${databases}
      do
        tables_included=$(json_array_to_strlist ".jobs[${job_idx}].servers[${server_idx}].databases[0][\"${database}\"].tables_included")
        tables_excluded=$(json_array_to_strlist ".jobs[${job_idx}].servers[${server_idx}].databases[0][\"${database}\"].tables_excluded")
        database_configuration="Database: ${database}
Tables included: ${tables_included}
Tables excluded: ${tables_excluded}"
        if [ "${databases_configuration}" = "" ]; then
          databases_configuration="${database_configuration}"
        else
          databases_configuration="${databases_configuration}
${database_configuration}"
        fi

      done

      entry_server="Postgres server: ${PGHOST}
Postgres port: ${PGPORT}
Postgres username: ${PGUSERNAME}
Backup path: ${backuppath}
Filename date: ${filenamedate}
Compress: ${compress}
Configured databases: ${databases}
Excluded databases: ${databases_excluded}"

      if [ ! "${databases_configuration}" = "" ]; then
      entry_server="${entry_server}
Database configuration:
${databases_configuration}"
      fi

      if [ "${entry_servers}" = "" ]; then
        entry_servers="${entry_server}"
      else
        entry_servers="${entry_servers}
${entry_server}"
      fi

    done

    configuration="Schedule: ${crontab}
Debug: ${debug}
${entry_servers}"

  fi
  
  echo "${configuration}"
}


# Init

mkdir -p /persistent-data/logs
rm -rf /tmp/LOCKFILE_*

log "Vendanor CloudDump v${VERSION} Start ($0)"

# Set up signal handlers
trap 'shutdown_handler' SIGTERM SIGINT


# Check commands

cmds="which grep sed cut cp chmod mkdir bc jq mail mutt postconf postmap ssh sshfs smbnetfs lockfile"
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

mail_body="CloudDump ${HOST}

STARTED

Debug: ${DEBUG}
SMTP server: ${SMTPSERVER}
"

if [ ! "${mounts_summary}" = "" ]; then
  mail_body="${mail_body}
Mountpoints:
${mounts_summary}
"
fi

  mail_body="${mail_body}
JOBS

${jobs_summary}
"

mail_body="${mail_body}
Vendanor CloudDump v${VERSION}"

if [ "${MAIL}" = "mutt" ]; then
  echo "${mail_body}" | EMAIL="${MAILFROM} <${MAILFROM}>" ${MAIL} -s "[Started] CloudDump ${HOST}" "${MAILTO}"
else
  echo "${mail_body}" | ${MAIL} -r "${MAILFROM} <${MAILFROM}>" -s "[Started] CloudDump ${HOST}" "${MAILTO}"
fi

log "Startup email sent."


# Helper function to check if cron pattern matches current time
check_cron_match() {
  local cron_pattern="$1"
  local current_min
  local current_hour
  local current_day
  local current_month
  local current_dow
  current_min=$(date '+%-M')
  current_hour=$(date '+%-H')
  current_day=$(date '+%-d')
  current_month=$(date '+%-m')
  current_dow=$(date '+%u')  # 1-7, Monday is 1
  
  # Convert Sunday from 7 to 0 for cron compatibility
  if [ "${current_dow}" = "7" ]; then
    current_dow="0"
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
  
  if check_field "${cron_min}" "${current_min}" && \
     check_field "${cron_hour}" "${current_hour}" && \
     check_field "${cron_day}" "${current_day}" && \
     check_field "${cron_month}" "${current_month}" && \
     check_field "${cron_dow}" "${current_dow}"; then
    return 0
  fi
  
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
    
    script="dump_${type}.sh"
    
    crontab=$(jq -r ".jobs[${i}].crontab" "${CONFIGFILE}" | sed 's/^null$//g')
    if [ $? -ne 0 ] || [ "${crontab}" = "" ]; then
      continue
    fi
    
    jobdebug=$(jq -r ".jobs[${i}].debug" "${CONFIGFILE}")
    
    # Initialize last run time if not set
    if [ -z "${last_run_times[${jobid}]}" ]; then
      last_run_times[${jobid}]="0"
    fi
    
    # Check if cron pattern matches current time
    if check_cron_match "${crontab}"; then
      
      # Get last run minute for this job
      last_run_minute=$(date -d "@${last_run_times[${jobid}]}" '+%Y-%m-%d %H:%M' 2>/dev/null)
      
      # Only run if we haven't run this job in the current minute
      if [ "${last_run_minute}" != "${current_minute}" ]; then
        
        log "Running job ${jobid} (script: ${script})"
        
        # Get script path
        echo "${script}" | grep '^\/' >/dev/null 2>&1
        if [ $? -eq 0 ]; then
          scriptfile="${script}"
          scriptfilename=$(echo "${script}" | sed 's/.*\///g')
        else
          scriptfile=$(which "${script}" 2>/dev/null)
          if [ "${scriptfile}" = "" ]; then
            scriptfile="/usr/local/bin/${script}"
          fi
          scriptfilename="${script}"
        fi
        
        # Create lockfile
        LOCKFILE="/tmp/LOCKFILE_${scriptfilename}_${jobid}"
        LOCKFILE=$(echo "${LOCKFILE}" | sed 's/\.//g')
        
        # Check if already running
        lockfile -r 0 "${LOCKFILE}" >/dev/null 2>&1
        if [ $? -ne 0 ]; then
          log "Job ${jobid} already running, skipping."
        else
          # Create log file
          RANDOM=$$
          LOGFILE="/tmp/vnclouddump-${jobid}-${RANDOM}.log"
          
          time_start=$(date +%s)
          time_start_timestamp=$(timestamp)
          
          log "Job ${jobid} starting at ${time_start_timestamp}" >> "${LOGFILE}"
          
          # Run the script directly
          if [ "${jobdebug}" = "true" ]; then
            /bin/bash -x "${scriptfile}" "${jobid}" >> "${LOGFILE}" 2>&1
            result=$?
          else
            /bin/bash "${scriptfile}" "${jobid}" >> "${LOGFILE}" 2>&1
            result=$?
          fi
          
          time_end=$(date +%s)
          
          log "Job ${jobid} finished at $(timestamp)" >> "${LOGFILE}"
          
          if [ ${result} -eq 0 ]; then
            log "Job ${jobid} completed successfully"
          else
            log "Job ${jobid} completed with errors (exit code: ${result})"
          fi
          
          # Get configuration for email
          configuration=$(get_job_configuration "${jobid}" "${script}")
          
          # Send email report
          send_job_email "${jobid}" "${script}" "${result}" "${time_start}" "${time_end}" "${time_start_timestamp}" "${LOGFILE}" "${configuration}"
          
          # Clean up log file
          rm -f "${LOGFILE}"
          
          # Remove lockfile
          rm -f "${LOCKFILE}"
        fi
        
        # Update last run time for this job
        last_run_times[${jobid}]=$(date +%s)
        
      else
        log "Skipping job ${jobid} - already ran in current minute"
      fi
      
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
