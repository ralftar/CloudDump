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


# Init

mkdir -p /persistent-data/logs
rm -rf /tmp/LOCKFILE_*

log "Vendanor CloudDump v${VERSION} Start ($0)"


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

/usr/sbin/postfix start || exit 1


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

  if [ "${jobs_summary}" = "" ]; then
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
      
      # Create config and credential files in memory
      mkdir -p /dev/shm || exit 1
      
      smbnetfs_config="/dev/shm/smbnetfs_${i}.conf"
      smbcredentials="/dev/shm/.smbcredentials_${i}"
      
      # Set up credentials if username is provided
      if [ ! "${username}" = "" ]; then
        echo "auth ${smbcredentials}" > "${smbnetfs_config}" || exit 1
        if [ "${password}" = "" ]; then
          echo -e "${username}\n" > "${smbcredentials}" || exit 1
        else
          echo -e "${username}\n${password}" > "${smbcredentials}" || exit 1
        fi
        chmod 600 "${smbcredentials}" || exit 1
      else
        # Guest access
        echo "auth ${smbcredentials}" > "${smbnetfs_config}" || exit 1
        echo -e "guest\n" > "${smbcredentials}" || exit 1
        chmod 600 "${smbcredentials}" || exit 1
      fi
      
      log "Mounting ${path} to ${mountpoint} using smbnetfs."
      
      # Create a base mount point for smbnetfs
      smbnetfs_root="/tmp/smbnetfs_${i}"
      mkdir -p "${smbnetfs_root}" || exit 1
      
      # Mount using smbnetfs to the root directory
      smbnetfs "${smbnetfs_root}" -o config="${smbnetfs_config}",allow_other || exit 1
      
      # Wait a moment for the mount to be ready
      sleep 2
      
      # Create a symlink to the actual share path
      ln -sf "${smbnetfs_root}/${smb_host}/${smb_share}" "${mountpoint}" || exit 1
      
      continue
    fi
    error "Invalid path ${path} for mountpoint ${mountpoint}."
    error "Syntax is \"user@host:/path\" for SSH, or \"//host/path\" for SMB."
    exit 1
  done
fi


#tail -f /var/log/postfix.log


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

  script=$(jq -r ".jobs[${i}].script" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ] || [ "${script}" = "" ]; then
    error "Missing script for job ID ${jobid}."
    continue
  fi

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
  local current_min=$(date '+%-M')
  local current_hour=$(date '+%-H')
  local current_day=$(date '+%-d')
  local current_month=$(date '+%-m')
  local current_dow=$(date '+%u')  # 1-7, Monday is 1
  
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
      local step=$(echo "${field}" | sed 's|^\*/||')
      if [ $((value % step)) -eq 0 ]; then
        return 0
      fi
      return 1
    fi
    
    # Handle ranges (e.g., 1-5)
    if echo "${field}" | grep -q '^[0-9]\+-[0-9]\+$'; then
      local start=$(echo "${field}" | cut -d'-' -f1)
      local end=$(echo "${field}" | cut -d'-' -f2)
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
  
  current_timestamp=$(date +%s)
  current_minute=$(date '+%Y-%m-%d %H:%M')
  
  # Check each job
  for ((i = 0; i < jobs; i++)); do
    
    jobid=$(jq -r ".jobs[${i}].id" "${CONFIGFILE}" | sed 's/^null$//g')
    if [ $? -ne 0 ] || [ "${jobid}" = "" ]; then
      continue
    fi
    
    script=$(jq -r ".jobs[${i}].script" "${CONFIGFILE}" | sed 's/^null$//g')
    if [ $? -ne 0 ] || [ "${script}" = "" ]; then
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
    
    # Check if cron pattern matches current time
    if check_cron_match "${crontab}"; then
      
      # Get last run minute for this job
      last_run_minute=$(date -d "@${last_run_times[${jobid}]}" '+%Y-%m-%d %H:%M' 2>/dev/null)
      
      # Only run if we haven't run this job in the current minute
      if [ "${last_run_minute}" != "${current_minute}" ]; then
        
        log "Running job ${jobid} (script: ${script})"
        
        # Run the wrapper script with the job
        if [ "${DEBUG}" = "true" ]; then
          /bin/bash -x /usr/local/bin/wrapper.sh "${script}" "${jobid}" "${jobdebug}"
        else
          /bin/bash /usr/local/bin/wrapper.sh "${script}" "${jobid}" "${jobdebug}"
        fi
        
        result=$?
        if [ ${result} -eq 0 ]; then
          log "Job ${jobid} completed successfully"
        else
          log "Job ${jobid} completed with errors (exit code: ${result})"
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
  sleep ${sleep_seconds}
  
done
