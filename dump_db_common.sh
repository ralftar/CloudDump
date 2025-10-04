#!/bin/bash

# Vendanor Database Dump Common Functions
# Shared functions and logic for database dump scripts
# This script is meant to be sourced by dump_pgsql.sh and dump_mysql.sh

# Note: The sourcing script must define:
# - DB_TYPE (e.g., "PostgreSQL", "MySQL")
# - check_db_commands() - function to check database-specific commands
# - list_databases() - function to list all databases
# - list_tables() - function to list tables in a database
# - dump_database() - function to dump a database

CONFIGFILE="/config/config.json"

# Common functions

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

print() {
  echo "[$(timestamp)] $*"
}

errorprint() {
  echo "[$(timestamp)] ERROR: $*" >&2
}

error() {
  errorprint "$@"
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

# Common initialization
common_init() {
  local script_name="$1"
  
  print "Vendanor ${DB_TYPE}Dump ($script_name)"

  # Check common commands
  local common_cmds="which grep sed cut date touch mkdir cp rm jq bzip2"
  local cmds_missing=
  
  for cmd in ${common_cmds}
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

  # Check database-specific commands
  check_db_commands
  local db_cmds_missing=$?
  
  if ! [ "${cmds_missing}" = "" ] || [ ${db_cmds_missing} -ne 0 ]; then
    if ! [ "${cmds_missing}" = "" ]; then
      error "Missing \"${cmds_missing}\" commands."
    fi
    exit 1
  fi
}

# Validate parameters
validate_parameters() {
  local jobid="$1"
  
  if [ "${jobid}" = "" ]; then
    error "Missing Job ID."
    exit 1
  fi

  if [ ! -f "${CONFIGFILE}" ]; then
    error "Missing Json configuration file ${CONFIGFILE}."
    exit 1
  fi

  if [ ! -r "${CONFIGFILE}" ]; then
    error "Can't read Json configuration file ${CONFIGFILE}."
    exit 1
  fi
}

# Find job index by job ID
find_job_index() {
  local jobid="$1"
  
  jobs=$(jq -r ".jobs | length" "${CONFIGFILE}")
  if [ "${jobs}" = "" ] || [ -z "${jobs}" ] || ! [ "${jobs}" -eq "${jobs}" ] 2>/dev/null; then
    error "Can't read jobs from Json configuration."
    exit 1
  fi

  local job_idx=
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
    error "No job ID ${jobid} in Json configuration."
    exit 1
  fi
  
  echo "${job_idx}"
}

# Create and validate backup path
prepare_backup_path() {
  local backuppath="$1"
  
  print "Creating backuppath ${backuppath}..."
  
  mkdir -p "${backuppath}"
  if [ $? -ne 0 ]; then
    error "Could not create backuppath ${backuppath}."
    return 1
  fi

  print "Checking permission for backuppath ${backuppath}..."
  
  touch "${backuppath}/TEST_FILE"
  if [ $? -ne 0 ]; then
    error "Could not access ${backuppath}."
    return 1
  fi

  rm -f "${backuppath}/TEST_FILE"
  return 0
}

# Compress backup file
compress_backup() {
  local backupfile="$1"
  local compress="$2"
  
  if [ "${compress}" = "true" ]; then
    print "BZipping ${backupfile}..."
    if [ -f "${backupfile}.bz2" ]; then
      rm -v "${backupfile}.bz2"
      if [ $? -ne 0 ]; then
        error "Failed to delete old backupfile ${backupfile}.bz2."
        return 1
      fi
    fi
    bzip2 "${backupfile}"
    if [ $? -eq 0 ]; then
      echo "${backupfile}.bz2"
      return 0
    else
      return 1
    fi
  else
    echo "${backupfile}"
    return 0
  fi
}

# Validate backup file
validate_backup_file() {
  local backupfile="$1"
  local dbhost="$2"
  local database="$3"
  
  if ! [ -f "${backupfile}" ]; then
    error "Backupfile ${backupfile} missing for ${database} on ${dbhost}."
    rm -f "${backupfile}"
    return 1
  fi

  size=$(wc -c "${backupfile}" | cut -d ' ' -f 1)
  if [ $? -ne 0 ]; then
    error "Could not get filesize for backupfile ${backupfile} of ${database} on ${dbhost}."
    rm -f "${backupfile}"
    return 1
  fi

  if [ -z "${size}" ] || ! [ "${size}" -eq "${size}" ] 2>/dev/null; then
    error "Invalid filesize for backupfile ${backupfile} of ${database} on ${dbhost}"
    rm -f "${backupfile}"
    return 1
  fi

  if [ "${size}" -lt 10 ]; then
    error "Backupfile ${backupfile} of ${database} on ${dbhost} too small (${size} bytes)."
    rm -f "${backupfile}"
    return 1
  fi
  
  return 0
}

# Determine databases to backup
determine_databases_to_backup() {
  local databases_all="$1"
  local databases_configured="$2"
  local databases_excluded="$3"
  local dbhost="$4"
  
  local databases_backup=""
  
  print "All databases: ${databases_all}"
  print "Configured databases: ${databases_configured}"
  print "Excluded databases: ${databases_excluded}"

  # Determine which databases to backup
  # If databases are explicitly configured, use only those
  # Otherwise, use all databases (excluding those in databases_excluded)
  if ! [ "${databases_configured}" = "" ]; then
    # Use only explicitly configured databases
    for database in ${databases_configured}
    do
      database_lc=$(echo "${database}" | tr '[:upper:]' '[:lower:]')
      
      # Check if database exists
      found=0
      for database_available in ${databases_all}
      do
        database_available_lc=$(echo "${database_available}" | tr '[:upper:]' '[:lower:]')
        if [ "${database_available_lc}" = "${database_lc}" ]; then
          found=1
          break
        fi
      done
      
      if [ "${found}" = "0" ]; then
        error "Configured database '${database}' does not exist on ${dbhost}."
        result=1
        continue
      fi
      
      if [ "${databases_backup}" = "" ]; then
        databases_backup="${database}"
      else
        databases_backup="${databases_backup} ${database}"
      fi
    done
  else
    # Use all databases, excluding those in databases_excluded
    for database in ${databases_all}
    do
      database_lc=$(echo "${database}" | tr '[:upper:]' '[:lower:]')
      
      # Check if database is excluded
      if ! [ "${databases_excluded}" = "" ]; then
        exclude=0
        for database_exclude in ${databases_excluded}
        do
          database_exclude_lc=$(echo "${database_exclude}" | tr '[:upper:]' '[:lower:]')
          if [ "${database_exclude_lc}" = "${database_lc}" ]; then
            exclude=1
            break
          fi
        done
        if [ "${exclude}" = "1" ]; then
          continue
        fi
      fi
      
      if [ "${databases_backup}" = "" ]; then
        databases_backup="${database}"
      else
        databases_backup="${databases_backup} ${database}"
      fi
    done
  fi

  if [ "${databases_backup}" = "" ]; then
    error "Missing databases to backup for ${dbhost}."
    return 1
  fi

  print "Databases to backup: ${databases_backup}"
  echo "${databases_backup}"
  return 0
}
