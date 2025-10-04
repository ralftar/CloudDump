#!/bin/bash

# Vendanor PgDump Script
# This script runs pg_dump for each database on each server for the specified job
# Usage: dump_pgsql.sh <jobid>

# Source common database dump functions
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/dump_db_common.sh" || exit 1

# Database-specific configuration
DB_TYPE="PostgreSQL"
JOBID="${1}"

# Check database-specific commands
check_db_commands() {
  local db_cmds="psql pg_dump tar"
  local cmds_missing=
  
  for cmd in ${db_cmds}
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
    return 1
  fi
  return 0
}

# List all databases for PostgreSQL
list_databases() {
  local host="$1"
  local port="$2"
  local user="$3"
  local pass="$4"
  
  PGPASSWORD=${pass} psql -h "${host}" -p "${port}" -U "${user}" -l
  if [ $? -ne 0 ]; then
    return 1
  fi

  databases_all=$(PGPASSWORD=${pass} psql -h "${host}" -p "${port}" -U "${user}" -l | grep '|' | sed 's/ //g' | grep -v '^Name|' | grep -v '^||' | cut -d '|' -f 1 | sed -z 's/\n/ /g;s/ $/\n/')
  if [ $? -ne 0 ]; then
    return 1
  fi

  echo "${databases_all}"
  return 0
}

# List tables in a PostgreSQL database
list_tables() {
  local host="$1"
  local port="$2"
  local user="$3"
  local pass="$4"
  local database="$5"
  
  tables_all=$(PGPASSWORD=${pass} psql -h "${host}" -p "${port}" -U "${user}" -d "${database}" -t -c "SELECT tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema');" 2>&1 | sed 's/^ *//g' | sed 's/ *$//g' | grep -v '^$' | sed -z 's/\n/ /g;s/ $/\n/')
  if [ $? -ne 0 ]; then
    return 1
  fi
  
  echo "${tables_all}"
  return 0
}

# Initialize
common_init "$0"
validate_parameters "${JOBID}"
job_idx=$(find_job_index "${JOBID}")


# Iterate servers

result=0

server_count=$(jq -r ".jobs[${job_idx}].servers | length" "${CONFIGFILE}")
if [ "${server_count}" = "" ] || [ -z "${server_count}" ] || ! [ "${server_count}" -eq "${server_count}" ] 2>/dev/null; then
  error "Can't read servers for ${JOBID} from Json configuration."
  exit 1
fi

if [ "${server_count}" -eq 0 ]; then
  error "No servers for ${JOBID} in Json configuration."
  exit 1
fi


for ((server_idx = 0; server_idx < server_count; server_idx++)); do

  # Reset databases_backup for each server
  databases_backup=""

  PGHOST=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].host" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ] || [ "${PGHOST}" = "" ]; then
    error "Missing host for server at index ${server_idx} for job ID ${JOBID}."
    result=1
    continue
  fi

  print "Checking server ${PGHOST} (${server_idx}) for job ID ${job_idx}..."

  PGPORT=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].port" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ]; then
    PGPORT="5432"
  fi

  PGUSERNAME=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].user" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ] || [ "${PGUSERNAME}" = "" ]; then
    error "Missing user for server ${PGHOST}."
    result=1
    continue
  fi

  PGPASSWORD=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].pass" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ] || [ "${PGPASSWORD}" = "" ]; then
    error "Missing pass for ${PGHOST}."
    result=1
    continue
  fi

  backuppath=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].backuppath" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ] || [ "${backuppath}" = "" ]; then
    error "Missing backuppath for ${PGHOST}."
    continue
  fi

  filenamedate=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].filenamedate" "${CONFIGFILE}" | sed 's/^null$//g')
  compress=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].compress" "${CONFIGFILE}" | sed 's/^null$//g')

  # Get list of databases with explicit configuration
  databases_configured=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].databases[] | keys[]" "${CONFIGFILE}" 2>/dev/null | tr '\n' ' ')
  databases_excluded=$(json_array_to_strlist ".jobs[${job_idx}].servers[${server_idx}].databases_excluded")

  print "Listing databases for ${PGHOST}..."

  databases_all=$(list_databases "${PGHOST}" "${PGPORT}" "${PGUSERNAME}" "${PGPASSWORD}")
  if [ $? -ne 0 ] || [ "${databases_all}" = "" ]; then
    error "Failed to list databases for ${PGHOST}."
    result=1
    continue
  fi

  databases_backup=$(determine_databases_to_backup "${databases_all}" "${databases_configured}" "${databases_excluded}" "${PGHOST}")
  if [ $? -ne 0 ]; then
    result=1
    continue
  fi

  # Create backup path
  if ! prepare_backup_path "${backuppath}"; then
    result=1
    continue
  fi

  # Run pg_dump for each database

  for database in ${databases_backup}; do

    # Read the configuration for this database

    tables_excluded=
    tables_included=
    tables_excluded_params=
    tables_included_params=

    db_count=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].databases | length" "${CONFIGFILE}")
    if [ "${db_count}" = "" ] || [ -z "${db_count}" ] || ! [ "${db_count}" -eq "${db_count}" ] 2>/dev/null; then
      error "Can't read database configuration for ${PGHOST} from Json configuration."
      result=1
      continue
    fi

    for ((db_idx = 0; db_idx < db_count; db_idx++)); do

      # Check if this is the correct array index for this database.
      jq_output=$(jq -r ".jobs[${job_idx}].servers[${server_idx}].databases[${db_idx}][\"${database}\"] | length" "${CONFIGFILE}" | sed 's/^null$//g')
      if [ "${jq_output}" = "" ] || [ -z "${jq_output}" ] || ! [ "${jq_output}" -eq "${jq_output}" ] || [ "${jq_output}" -eq 0 ] 2>/dev/null; then
        continue
      fi

      # Read excluded tables
      tb_count=$(jq -r ".jobs[${job_idx}].servers[${server_idx}][\"databases\"][${db_idx}][\"${database}\"].tables_excluded | length" "${CONFIGFILE}")
      for ((tb_idx = 0; tb_idx < tb_count; tb_idx++)); do
        table_excluded=$(jq -r ".jobs[${job_idx}].servers[${server_idx}][\"databases\"][${db_idx}][\"${database}\"].tables_excluded[${tb_idx}]" "${CONFIGFILE}" | sed 's/^null$//g')
        if [ "${table_excluded}" = "" ]; then
          continue
        fi
        if [ "${tables_excluded}" = "" ]; then
          tables_excluded="$table_excluded"
          tables_excluded_params="--exclude-table=$table_excluded"
        else
          tables_excluded="${tables_excluded}, ${table_excluded}"
          tables_excluded_params="${tables_excluded_params} --exclude-table=${table_excluded}"
        fi
      done

      # Read included tables
      tb_count=$(jq -r ".jobs[${job_idx}].servers[${server_idx}][\"databases\"][${db_idx}][\"${database}\"].tables_included | length" "${CONFIGFILE}")
      for ((tb_idx = 0; tb_idx < tb_count; tb_idx++)); do
        table_included=$(jq -r ".jobs[${job_idx}].servers[${server_idx}][\"databases\"][${db_idx}][\"${database}\"].tables_included[${tb_idx}]" "${CONFIGFILE}" | sed 's/^null$//g')
        if [ "${table_included}" = "" ]; then
          continue
        fi
        if [ "${tables_included}" = "" ]; then
          tables_included="$table_included"
          tables_included_params="--table=$table_included"
        else
          tables_included="${tables_included}, ${table_included}"
          tables_included_params="${tables_included_params} --table=${table_included}"
        fi
      done

      break

    done

    BACKUPFILE_TEMP="${backuppath}/${database}-$(date '+%Y%m%d%H%M%S').tar"
    if [ "${filenamedate}" = "true" ]; then
      BACKUPFILE_FINAL="${BACKUPFILE_TEMP}"
    else
      BACKUPFILE_FINAL="${backuppath}/${database}.tar"
    fi

    print "Running pg_dump of ${database} for ${PGHOST} to backupfile ${BACKUPFILE_FINAL}..."

    # Fetch list of all tables if we need to validate includes or excludes
    tables_all=""
    if ! [ "${tables_included}" = "" ] || ! [ "${tables_excluded}" = "" ]; then
      print "Fetching table list for ${database}..."
      tables_all=$(list_tables "${PGHOST}" "${PGPORT}" "${PGUSERNAME}" "${PGPASSWORD}" "${database}")
      if [ $? -ne 0 ]; then
        error "Failed to list tables for ${database} on ${PGHOST}."
        result=1
        continue
      fi
    fi

    if [ "${tables_included}" = "" ]; then
      print "All tables for ${database} included"
    else
      print "Tables included for ${database}: ${tables_included}"
      
      # Validate that all included tables exist
      print "Validating included tables for ${database}..."
      
      # Validate tables and build params only for existing tables
      tables_included_validated=""
      tables_included_params=""
      for table_include in ${tables_included//,/ }
      do
        table_include=$(echo "${table_include}" | xargs)
        table_include_lc=$(echo "${table_include}" | tr '[:upper:]' '[:lower:]')
        found=0
        for table_available in ${tables_all}
        do
          table_available_lc=$(echo "${table_available}" | tr '[:upper:]' '[:lower:]')
          if [ "${table_available_lc}" = "${table_include_lc}" ]; then
            found=1
            break
          fi
        done
        if [ "${found}" = "0" ]; then
          error "Included table '${table_include}' does not exist in database '${database}' on ${PGHOST}. Skipping this table."
          result=1
        else
          # Only add existing tables to params
          if [ "${tables_included_validated}" = "" ]; then
            tables_included_validated="$table_include"
            tables_included_params="--table=$table_include"
          else
            tables_included_validated="${tables_included_validated}, ${table_include}"
            tables_included_params="${tables_included_params} --table=${table_include}"
          fi
        fi
      done
      
      # If none of the specified tables exist, skip this database
      if [ "${tables_included_validated}" = "" ]; then
        error "None of the specified tables exist in ${database} on ${PGHOST}. Skipping database backup."
        result=1
        continue
      fi
      
      tables_included="${tables_included_validated}"
    fi

    # Validate excluded tables (warnings only, don't fail)
    if ! [ "${tables_excluded}" = "" ]; then
      print "Validating excluded tables for ${database}..."
      
      for table_exclude in ${tables_excluded//,/ }
      do
        table_exclude=$(echo "${table_exclude}" | xargs)
        table_exclude_lc=$(echo "${table_exclude}" | tr '[:upper:]' '[:lower:]')
        found=0
        for table_available in ${tables_all}
        do
          table_available_lc=$(echo "${table_available}" | tr '[:upper:]' '[:lower:]')
          if [ "${table_available_lc}" = "${table_exclude_lc}" ]; then
            found=1
            break
          fi
        done
        if [ "${found}" = "0" ]; then
          print "WARNING: Excluded table '${table_exclude}' does not exist in database '${database}' on ${PGHOST}."
        fi
      done
      
      print "Tables excluded for ${database}: ${tables_excluded}"
    fi

    PGPASSWORD=${PGPASSWORD} pg_dump -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSERNAME}" -d "${database}" -F tar ${tables_included_params} ${tables_excluded_params} > "${BACKUPFILE_TEMP}"
    if [ $? -ne 0 ]; then
      error "pg_dump for ${database} on ${PGHOST} to backupfile ${BACKUPFILE_FINAL} failed."
      rm -f "${BACKUPFILE_TEMP}"
      result=1
      continue
    fi

    if ! validate_backup_file "${BACKUPFILE_TEMP}" "${PGHOST}" "${database}"; then
      result=1
      continue
    fi

    if ! [ "${BACKUPFILE_TEMP}" = "${BACKUPFILE_FINAL}" ]; then
      mv -v "${BACKUPFILE_TEMP}" "${BACKUPFILE_FINAL}"
      if [ $? -ne 0 ]; then
        error "Failed to rename backupfile ${BACKUPFILE_TEMP} to ${BACKUPFILE_FINAL}."
        rm -f "${BACKUPFILE_TEMP}"
        result=1
        continue
      fi
    fi

    print "Backup of ${database} on ${PGHOST} to backupfile ${BACKUPFILE_FINAL} is successful."

    BACKUPFILE_FINAL=$(compress_backup "${BACKUPFILE_FINAL}" "${compress}")
    if [ $? -ne 0 ]; then
      result=1
      continue
    fi

    print "Backup of ${database} on ${PGHOST} to backupfile ${BACKUPFILE_FINAL} complete"

  done

done


if ! [ "${result}" = "" ]; then
  exit "${result}"
fi
