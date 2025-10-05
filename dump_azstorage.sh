#!/bin/bash

# Vendanor AzDump Script
# This script runs azcopy sync
# Usage:
#   dump_azstorage.sh [-s source] [-d destination] [-m mirror]
#
# Example:
#   dump_azstorage.sh -s https://example.blob.core.windows.net/container?SAS -d /backups/azure -m false

# ----------------------------
# Default values
# ----------------------------
SOURCE=""
DESTINATION=""
DELETE_DESTINATION="true"

# ----------------------------
# Parse command-line arguments
# ----------------------------
while getopts "s:d:m:" opt; do
  case ${opt} in
    s )
      SOURCE="${OPTARG}"
      ;;
    d )
      DESTINATION="${OPTARG}"
      ;;
    m )
      DELETE_DESTINATION="${OPTARG}"
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

log_info "Vendanor AzDump ($0)"


# Check commands

cmds="which sed date touch mkdir rm azcopy"
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
  log_error "Missing \"${cmds_missing}\" commands."
  exit 1
fi


# Check parameters

if [ "${SOURCE}" = "" ]; then
  log_error "Missing source parameter (-s)."
  exit 1
fi

if [ "${DESTINATION}" = "" ]; then
  log_error "Missing destination parameter (-d)."
  exit 1
fi

# Ensure delete_destination is boolean
if [ "${DELETE_DESTINATION}" != "true" ] && [ "${DELETE_DESTINATION}" != "false" ]; then
  DELETE_DESTINATION="true"
fi

source_stripped=$(echo "${SOURCE}" | cut -d '?' -f 1)

log_info "Source: ${source_stripped}"
log_info "Destination: ${DESTINATION}"
log_info "Mirror (delete): ${DELETE_DESTINATION}"


# Validate source

echo "${SOURCE}" | grep "^https:\/\/.*" >/dev/null 2>&1
if [ $? -ne 0 ]; then
  log_error "Invalid source. Source must start with https://"
  exit 1
fi


# Create directory

log_info "Creating directory for destination ${DESTINATION}"

mkdir -p "${DESTINATION}"
if [ $? -ne 0 ]; then
  log_error "Could not create directory ${DESTINATION}"
  exit 1
fi


# Check permissions

log_info "Checking permission for destination ${DESTINATION}"

touch "${DESTINATION}/TEST_FILE"
if [ $? -ne 0 ]; then
  log_error "Could not access ${DESTINATION}."
  exit 1
fi

rm -f "${DESTINATION}/TEST_FILE"


# Run azcopy

log_info "Syncing source ${source_stripped} to destination ${DESTINATION}..."

azcopy sync --recursive --delete-destination="${DELETE_DESTINATION}" "${SOURCE}" "${DESTINATION}"
result=$?

if [ ${result} -ne 0 ]; then
  log_error "Sync from source ${source_stripped} to destination ${DESTINATION} failed."
  exit ${result}
fi

log_info "Sync completed successfully."
