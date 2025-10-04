#!/bin/bash

# Vendanor PgDump Script
# This script runs pg_dump for a single database
# Usage:
#   dump_pgsql.sh [-h host] [-p port] [-U user] [-P password] [-d database]
#                 [-b backuppath] [-f filename_date] [-z compress]
#                 [-i tables_included] [-x tables_excluded]
#
# Example:
#   dump_pgsql.sh -d mydb -U postgres -P secret -b /backups -z true

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
TABLES_INCLUDED=""
TABLES_EXCLUDED=""

# ----------------------------
# Parse command-line arguments
# ----------------------------
while getopts "h:p:U:P:d:b:f:z:i:x:" opt; do
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
    i )
      TABLES_INCLUDED="${OPTARG}"
      ;;
    x )
      TABLES_EXCLUDED="${OPTARG}"
      ;;
    \? )
      echo "Invalid option: -${OPTARG}" >&2
      exit 1
      ;;
  esac
done


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

cmds="which grep sed cut date touch mkdir rm psql pg_dump tar bzip2"
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
  error "Missing host parameter (-h)."
  exit 1
fi

if [ "${PGUSERNAME}" = "" ]; then
  error "Missing user parameter (-U)."
  exit 1
fi

if [ "${PGPASSWORD}" = "" ]; then
  error "Missing password parameter (-P)."
  exit 1
fi

if [ "${DATABASE}" = "" ]; then
  error "Missing database parameter (-d)."
  exit 1
fi

if [ "${BACKUPPATH}" = "" ]; then
  error "Missing backuppath parameter (-b)."
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

print "Database: ${DATABASE}"
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


# Build table parameters

tables_excluded_params=""
tables_included_params=""

if [ ! "${TABLES_EXCLUDED}" = "" ]; then
  print "Tables excluded: ${TABLES_EXCLUDED}"
  for table_excluded in ${TABLES_EXCLUDED//,/ }
  do
    table_excluded=$(echo "${table_excluded}" | xargs)
    if [ ! "${table_excluded}" = "" ]; then
      tables_excluded_params="${tables_excluded_params} --exclude-table=${table_excluded}"
    fi
  done
fi

if [ ! "${TABLES_INCLUDED}" = "" ]; then
  print "Tables included: ${TABLES_INCLUDED}"
  for table_included in ${TABLES_INCLUDED//,/ }
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
  exit 1
fi

if ! [ -f "${BACKUPFILE_TEMP}" ]; then
  error "Backupfile ${BACKUPFILE_TEMP} missing for ${DATABASE} on ${PGHOST}."
  rm -f "${BACKUPFILE_TEMP}"
  exit 1
fi

size=$(wc -c "${BACKUPFILE_TEMP}" | cut -d ' ' -f 1)
if [ $? -ne 0 ]; then
  error "Could not get filesize for backupfile ${BACKUPFILE_TEMP} of ${DATABASE} on ${PGHOST}."
  rm -f "${BACKUPFILE_TEMP}"
  exit 1
fi

if [ -z "${size}" ] || ! [ "${size}" -eq "${size}" ] 2>/dev/null; then
  error "Invalid filesize for backupfile ${BACKUPFILE_TEMP} of ${DATABASE} on ${PGHOST}"
  rm -f "${BACKUPFILE_TEMP}"
  exit 1
fi

if [ "${size}" -eq 0 ]; then
  error "Backupfile ${BACKUPFILE_TEMP} of ${DATABASE} on ${PGHOST} is empty."
  rm -f "${BACKUPFILE_TEMP}"
  exit 1
fi

print "pg_dump of ${DATABASE} completed. Backupfile size: ${size} bytes."


# Compress if needed

if [ "${COMPRESS}" = "true" ]; then
  print "Compressing backupfile ${BACKUPFILE_TEMP}..."
  
  bzip2 -f "${BACKUPFILE_TEMP}"
  if [ $? -ne 0 ]; then
    error "Compression of ${BACKUPFILE_TEMP} failed."
    exit 1
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
    exit 1
  fi
fi

print "Backup completed successfully: ${BACKUPFILE_FINAL}"
