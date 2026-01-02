#!/bin/bash

# Vendanor PgDump Script
# This script runs pg_dump for databases on a PostgreSQL server
# Usage:
#   dump_pgsql.sh [-h host] [-p port] [-U user] [-P password] [-d database]
#                 [-b backuppath] [-f filename_date] [-z compress]
#
# Example:
#   dump_pgsql.sh -U postgres -P secret -h localhost -b /backups -z true
#
# Note: Database selection and table filtering are controlled via environment variables:
#   - DATABASES_JSON: JSON array specifying databases and their table configurations
#   - DATABASES_EXCLUDED_JSON: JSON array of database names to exclude

# ----------------------------
# Default values
# ----------------------------
PGHOST=""
PGPORT="5432"
PGUSERNAME="postgres"
PGPASSWORD=""
DATABASE=""
BACKUPPATH=""
FILENAMEDATE="false"
COMPRESS="true"

# ----------------------------
# Parse command-line arguments
# ----------------------------
while getopts "h:p:U:P:d:b:f:z:" opt; do
  case ${opt} in
    h )
      PGHOST="${OPTARG}"
      ;;
    p )
      PGPORT="${OPTARG}"
      ;;
    U )
      PGUSERNAME="${OPTARG}"
      ;;
    P )
      PGPASSWORD="${OPTARG}"
      ;;
    d )
      DATABASE="${OPTARG}"
      ;;
    b )
      BACKUPPATH="${OPTARG}"
      ;;
    f )
      FILENAMEDATE="${OPTARG}"
      ;;
    z )
      COMPRESS="${OPTARG}"
      ;;
    \? )
      echo "Invalid option: -${OPTARG}" >&2
      exit 1
      ;;
  esac
done


# Functions

# Logs an informational message to stdout with timestamp prefix
#
# Arguments:
#   All arguments are concatenated and logged as the message
#
# Output:
#   [YYYY-MM-DD HH:MM:SS] message
#
log_info() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
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
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}


# Init

log_info "Vendanor PgDump ($0)"


# Check commands

cmds="which grep sed cut date touch mkdir rm psql pg_dump tar bzip2 jq"
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


# Check parameters

if [ "${PGHOST}" = "" ]; then
  log_error "Missing host parameter (-h)."
  exit 1
fi

if [ "${PGUSERNAME}" = "" ]; then
  log_error "Missing user parameter (-U)."
  exit 1
fi

if [ "${PGPASSWORD}" = "" ]; then
  log_error "Missing password parameter (-P)."
  exit 1
fi

if [ "${BACKUPPATH}" = "" ]; then
  log_error "Missing backuppath parameter (-b)."
  exit 1
fi

# Ensure filenamedate is boolean
if [ "${FILENAMEDATE}" != "true" ] && [ "${FILENAMEDATE}" != "false" ]; then
  FILENAMEDATE="false"
fi

# Ensure compress is boolean
if [ "${COMPRESS}" != "true" ] && [ "${COMPRESS}" != "false" ]; then
  COMPRESS="false"
fi

if [ "${DATABASES_JSON}" = "" ]; then
  DATABASES_JSON="[]"
fi

if [ "${DATABASES_EXCLUDED_JSON}" = "" ]; then
  DATABASES_EXCLUDED_JSON="[]"
fi

log_info "Host: ${PGHOST}"
log_info "Port: ${PGPORT}"
log_info "Username: ${PGUSERNAME}"
log_info "Backup path: ${BACKUPPATH}"
log_info "Filename date: ${FILENAMEDATE}"
log_info "Compress: ${COMPRESS}"


# Create backup path

log_info "Creating backuppath ${BACKUPPATH}..."

if ! mkdir -p "${BACKUPPATH}"; then
  log_error "Could not create backuppath ${BACKUPPATH}."
  exit 1
fi


# Check permissions

log_info "Checking permission for backuppath ${BACKUPPATH}..."

if ! touch "${BACKUPPATH}/TEST_FILE"; then
  log_error "Could not access ${BACKUPPATH}."
  exit 1
fi

rm -f "${BACKUPPATH}/TEST_FILE"


# Check available disk space

log_info "Checking available disk space for backuppath ${BACKUPPATH}"

available_space=$(df -k "${BACKUPPATH}" | tail -1 | awk '{print $4}')
if [ "${available_space}" -lt 102400 ]; then
  log_error "Insufficient disk space at ${BACKUPPATH}. Available: ${available_space}KB (minimum 100MB required)"
  exit 1
fi
log_info "Available disk space: $((available_space / 1024))MB"


# Get list of all databases from server

log_info "Querying server for list of databases..."

if ! databases_all=$(PGPASSWORD=${PGPASSWORD} psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSERNAME}" -l 2>/dev/null | grep '|' | sed 's/ //g' | grep -v '^Name|' | grep -v '^||' | cut -d '|' -f 1); then
  log_error "Failed to query database list from ${PGHOST}."
  exit 1
fi


# Determine which databases to backup

databases_configured=$(echo "${DATABASES_JSON}" | jq -r '.[] | keys[]' 2>/dev/null | tr '\n' ' ')
databases_excluded=$(echo "${DATABASES_EXCLUDED_JSON}" | jq -r '.[]' 2>/dev/null | tr '\n' ' ')

databases_backup=""
if [ ! "${databases_configured}" = "" ]; then
  # Use only explicitly configured databases
  log_info "Using explicitly configured databases: ${databases_configured}"
  databases_backup="${databases_configured}"
else
  # Use all databases, excluding those in databases_excluded
  log_info "Using all databases except excluded ones"
  for database in ${databases_all}
  do
    skip=0
    for excluded in ${databases_excluded}
    do
      if [ "${database}" = "${excluded}" ]; then
        skip=1
        break
      fi
    done
    if [ ${skip} -eq 0 ]; then
      databases_backup="${databases_backup} ${database}"
    fi
  done
fi

if [ "${databases_backup}" = "" ]; then
  log_error "No databases to backup."
  exit 1
fi

log_info "Databases to backup: ${databases_backup}"


# Backup each database

overall_result=0
for DATABASE in ${databases_backup}
do
  log_info "Processing database: ${DATABASE}"
  
  # Get table configuration for this database
  tables_included=""
  tables_excluded=""
  
  # Look for database-specific configuration in DATABASES_JSON
  db_config=$(echo "${DATABASES_JSON}" | jq -r ".[] | select(has(\"${DATABASE}\")) | .\"${DATABASE}\"" 2>/dev/null)
  if [ ! "${db_config}" = "" ] && [ ! "${db_config}" = "null" ]; then
    tables_included=$(echo "${db_config}" | jq -r '.tables_included[]?' 2>/dev/null | tr '\n' ',' | sed 's/,$//')
    tables_excluded=$(echo "${db_config}" | jq -r '.tables_excluded[]?' 2>/dev/null | tr '\n' ',' | sed 's/,$//')
  fi
  
  # Build table parameters
  tables_excluded_params=""
  tables_included_params=""
  
  if [ ! "${tables_excluded}" = "" ]; then
    log_info "Tables excluded: ${tables_excluded}"
    for table_excluded in ${tables_excluded//,/ }
    do
      table_excluded=$(echo "${table_excluded}" | xargs)
      if [ ! "${table_excluded}" = "" ]; then
        tables_excluded_params="${tables_excluded_params} --exclude-table=${table_excluded}"
      fi
    done
  fi
  
  if [ ! "${tables_included}" = "" ]; then
    log_info "Tables included: ${tables_included}"
    for table_included in ${tables_included//,/ }
    do
      table_included=$(echo "${table_included}" | xargs)
      if [ ! "${table_included}" = "" ]; then
        tables_included_params="${tables_included_params} --table=${table_included}"
      fi
    done
  fi
  
  # Prepare backup file names
  BACKUPFILE_TEMP="${BACKUPPATH}/${DATABASE}-$(date '+%Y%m%d%H%M%S').tar"
  if [ "${FILENAMEDATE}" = "true" ]; then
    BACKUPFILE_FINAL="${BACKUPFILE_TEMP}"
  else
    BACKUPFILE_FINAL="${BACKUPPATH}/${DATABASE}.tar"
  fi
  
  # Run pg_dump
  log_info "Running pg_dump of ${DATABASE} for ${PGHOST} to backupfile ${BACKUPFILE_FINAL}..."
  
  # shellcheck disable=SC2086
  if ! PGPASSWORD=${PGPASSWORD} pg_dump -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSERNAME}" -d "${DATABASE}" -F tar ${tables_included_params} ${tables_excluded_params} > "${BACKUPFILE_TEMP}"; then
    log_error "pg_dump for ${DATABASE} on ${PGHOST} to backupfile ${BACKUPFILE_FINAL} failed."
    rm -f "${BACKUPFILE_TEMP}"
    overall_result=1
    continue
  fi
  
  if ! [ -f "${BACKUPFILE_TEMP}" ]; then
    log_error "Backupfile ${BACKUPFILE_TEMP} missing for ${DATABASE} on ${PGHOST}."
    rm -f "${BACKUPFILE_TEMP}"
    overall_result=1
    continue
  fi
  
  if ! size=$(wc -c "${BACKUPFILE_TEMP}" | cut -d ' ' -f 1); then
    log_error "Could not get filesize for backupfile ${BACKUPFILE_TEMP} of ${DATABASE} on ${PGHOST}."
    rm -f "${BACKUPFILE_TEMP}"
    overall_result=1
    continue
  fi
  
  if [ -z "${size}" ] || ! [ "${size}" -eq "${size}" ] 2>/dev/null; then
    log_error "Invalid filesize for backupfile ${BACKUPFILE_TEMP} of ${DATABASE} on ${PGHOST}"
    rm -f "${BACKUPFILE_TEMP}"
    overall_result=1
    continue
  fi
  
  if [ "${size}" -eq 0 ]; then
    log_error "Backupfile ${BACKUPFILE_TEMP} of ${DATABASE} on ${PGHOST} is empty."
    rm -f "${BACKUPFILE_TEMP}"
    overall_result=1
    continue
  fi
  
  log_info "pg_dump of ${DATABASE} completed. Backupfile size: ${size} bytes."
  
  # Compress if needed
  if [ "${COMPRESS}" = "true" ]; then
    log_info "Compressing backupfile ${BACKUPFILE_TEMP}..."
    
    if ! bzip2 -f "${BACKUPFILE_TEMP}"; then
      log_error "Compression of ${BACKUPFILE_TEMP} failed."
      overall_result=1
      continue
    fi
    
    BACKUPFILE_TEMP="${BACKUPFILE_TEMP}.bz2"
    if [ "${FILENAMEDATE}" = "true" ]; then
      BACKUPFILE_FINAL="${BACKUPFILE_FINAL}.bz2"
    else
      BACKUPFILE_FINAL="${BACKUPPATH}/${DATABASE}.tar.bz2"
    fi
    
    log_info "Compression completed. Compressed file: ${BACKUPFILE_TEMP}"
  fi
  
  # Move to final filename
  if [ ! "${BACKUPFILE_TEMP}" = "${BACKUPFILE_FINAL}" ]; then
    log_info "Moving ${BACKUPFILE_TEMP} to ${BACKUPFILE_FINAL}..."
    
    if ! mv "${BACKUPFILE_TEMP}" "${BACKUPFILE_FINAL}"; then
      log_error "Could not move ${BACKUPFILE_TEMP} to ${BACKUPFILE_FINAL}."
      overall_result=1
      continue
    fi
  fi
  
  log_info "Backup completed successfully: ${BACKUPFILE_FINAL}"
done

if [ ${overall_result} -eq 0 ]; then
  log_info "All database backups completed successfully."
else
  log_error "Some database backups failed."
fi

exit ${overall_result}
