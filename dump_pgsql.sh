#!/bin/bash

# Vendanor PgDump Script
# This script runs pg_dump for databases on a PostgreSQL server
# Usage: dump_pgsql.sh <host> <port> <user> <pass> <backuppath> <filenamedate> <compress> <databases_json> <databases_excluded_json>


# Parameters
PGHOST="${1}"
PGPORT="${2}"
PGUSERNAME="${3}"
PGPASSWORD="${4}"
BACKUPPATH="${5}"
FILENAMEDATE="${6}"
COMPRESS="${7}"
DATABASES_JSON="${8}"
DATABASES_EXCLUDED_JSON="${9}"


# Functions

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


# Init

print "Vendanor PgDump ($0)"


# Check commands

cmds="which grep sed cut date touch mkdir rm psql pg_dump tar bzip2 jq"
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


# Check parameters

if [ "${PGHOST}" = "" ]; then
  error "Missing host parameter."
  exit 1
fi

if [ "${PGPORT}" = "" ]; then
  PGPORT="5432"
fi

if [ "${PGUSERNAME}" = "" ]; then
  error "Missing user parameter."
  exit 1
fi

if [ "${PGPASSWORD}" = "" ]; then
  error "Missing pass parameter."
  exit 1
fi

if [ "${BACKUPPATH}" = "" ]; then
  error "Missing backuppath parameter."
  exit 1
fi

if [ "${FILENAMEDATE}" = "" ]; then
  FILENAMEDATE="false"
fi

if [ "${COMPRESS}" = "" ]; then
  COMPRESS="false"
fi

if [ "${DATABASES_JSON}" = "" ]; then
  DATABASES_JSON="[]"
fi

if [ "${DATABASES_EXCLUDED_JSON}" = "" ]; then
  DATABASES_EXCLUDED_JSON="[]"
fi

print "Host: ${PGHOST}"
print "Port: ${PGPORT}"
print "Username: ${PGUSERNAME}"
print "Backup path: ${BACKUPPATH}"
print "Filename date: ${FILENAMEDATE}"
print "Compress: ${COMPRESS}"


# Create backup path

print "Creating backuppath ${BACKUPPATH}..."

mkdir -p "${BACKUPPATH}"
if [ $? -ne 0 ]; then
  error "Could not create backuppath ${BACKUPPATH}."
  exit 1
fi


# Check permissions

print "Checking permission for backuppath ${BACKUPPATH}..."

touch "${BACKUPPATH}/TEST_FILE"
if [ $? -ne 0 ]; then
  error "Could not access ${BACKUPPATH}."
  exit 1
fi

rm -f "${BACKUPPATH}/TEST_FILE"


# Get list of all databases from server

print "Querying server for list of databases..."

databases_all=$(PGPASSWORD=${PGPASSWORD} psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSERNAME}" -l 2>/dev/null | grep '|' | sed 's/ //g' | grep -v '^Name|' | grep -v '^||' | cut -d '|' -f 1)
if [ $? -ne 0 ]; then
  error "Failed to query database list from ${PGHOST}."
  exit 1
fi


# Determine which databases to backup

databases_configured=$(echo "${DATABASES_JSON}" | jq -r '.[] | keys[]' 2>/dev/null | tr '\n' ' ')
databases_excluded=$(echo "${DATABASES_EXCLUDED_JSON}" | jq -r '.[]' 2>/dev/null | tr '\n' ' ')

databases_backup=""
if [ ! "${databases_configured}" = "" ]; then
  # Use only explicitly configured databases
  print "Using explicitly configured databases: ${databases_configured}"
  databases_backup="${databases_configured}"
else
  # Use all databases, excluding those in databases_excluded
  print "Using all databases except excluded ones"
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
  error "No databases to backup."
  exit 1
fi

print "Databases to backup: ${databases_backup}"


# Backup each database

overall_result=0
for DATABASE in ${databases_backup}
do
  print "Processing database: ${DATABASE}"
  
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
    print "Tables excluded: ${tables_excluded}"
    for table_excluded in ${tables_excluded//,/ }
    do
      table_excluded=$(echo "${table_excluded}" | xargs)
      if [ ! "${table_excluded}" = "" ]; then
        tables_excluded_params="${tables_excluded_params} --exclude-table=${table_excluded}"
      fi
    done
  fi
  
  if [ ! "${tables_included}" = "" ]; then
    print "Tables included: ${tables_included}"
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
  print "Running pg_dump of ${DATABASE} for ${PGHOST} to backupfile ${BACKUPFILE_FINAL}..."
  
  PGPASSWORD=${PGPASSWORD} pg_dump -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSERNAME}" -d "${DATABASE}" -F tar ${tables_included_params} ${tables_excluded_params} > "${BACKUPFILE_TEMP}"
  if [ $? -ne 0 ]; then
    error "pg_dump for ${DATABASE} on ${PGHOST} to backupfile ${BACKUPFILE_FINAL} failed."
    rm -f "${BACKUPFILE_TEMP}"
    overall_result=1
    continue
  fi
  
  if ! [ -f "${BACKUPFILE_TEMP}" ]; then
    error "Backupfile ${BACKUPFILE_TEMP} missing for ${DATABASE} on ${PGHOST}."
    rm -f "${BACKUPFILE_TEMP}"
    overall_result=1
    continue
  fi
  
  size=$(wc -c "${BACKUPFILE_TEMP}" | cut -d ' ' -f 1)
  if [ $? -ne 0 ]; then
    error "Could not get filesize for backupfile ${BACKUPFILE_TEMP} of ${DATABASE} on ${PGHOST}."
    rm -f "${BACKUPFILE_TEMP}"
    overall_result=1
    continue
  fi
  
  if [ -z "${size}" ] || ! [ "${size}" -eq "${size}" ] 2>/dev/null; then
    error "Invalid filesize for backupfile ${BACKUPFILE_TEMP} of ${DATABASE} on ${PGHOST}"
    rm -f "${BACKUPFILE_TEMP}"
    overall_result=1
    continue
  fi
  
  if [ "${size}" -eq 0 ]; then
    error "Backupfile ${BACKUPFILE_TEMP} of ${DATABASE} on ${PGHOST} is empty."
    rm -f "${BACKUPFILE_TEMP}"
    overall_result=1
    continue
  fi
  
  print "pg_dump of ${DATABASE} completed. Backupfile size: ${size} bytes."
  
  # Compress if needed
  if [ "${COMPRESS}" = "true" ]; then
    print "Compressing backupfile ${BACKUPFILE_TEMP}..."
    
    bzip2 -f "${BACKUPFILE_TEMP}"
    if [ $? -ne 0 ]; then
      error "Compression of ${BACKUPFILE_TEMP} failed."
      overall_result=1
      continue
    fi
    
    BACKUPFILE_TEMP="${BACKUPFILE_TEMP}.bz2"
    if [ "${FILENAMEDATE}" = "true" ]; then
      BACKUPFILE_FINAL="${BACKUPFILE_FINAL}.bz2"
    else
      BACKUPFILE_FINAL="${BACKUPPATH}/${DATABASE}.tar.bz2"
    fi
    
    print "Compression completed. Compressed file: ${BACKUPFILE_TEMP}"
  fi
  
  # Move to final filename
  if [ ! "${BACKUPFILE_TEMP}" = "${BACKUPFILE_FINAL}" ]; then
    print "Moving ${BACKUPFILE_TEMP} to ${BACKUPFILE_FINAL}..."
    
    mv "${BACKUPFILE_TEMP}" "${BACKUPFILE_FINAL}"
    if [ $? -ne 0 ]; then
      error "Could not move ${BACKUPFILE_TEMP} to ${BACKUPFILE_FINAL}."
      overall_result=1
      continue
    fi
  fi
  
  print "Backup completed successfully: ${BACKUPFILE_FINAL}"
done

if [ ${overall_result} -eq 0 ]; then
  print "All database backups completed successfully."
else
  error "Some database backups failed."
fi

exit ${overall_result}
