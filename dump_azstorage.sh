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
  if which "${cmd}" >/dev/null 2>&1; then
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

if ! echo "${SOURCE}" | grep "^https:\/\/.*" >/dev/null 2>&1; then
  log_error "Invalid source. Source must start with https://"
  exit 1
fi


# Create directory

log_info "Creating directory for destination ${DESTINATION}"

if ! mkdir -p "${DESTINATION}"; then
  log_error "Could not create directory ${DESTINATION}"
  exit 1
fi


# Check permissions

log_info "Checking permission for destination ${DESTINATION}"

if ! touch "${DESTINATION}/TEST_FILE"; then
  log_error "Could not access ${DESTINATION}."
  exit 1
fi

rm -f "${DESTINATION}/TEST_FILE"


# Check available disk space

log_info "Checking available disk space for destination ${DESTINATION}"

available_space=$(df -k "${DESTINATION}" | tail -1 | awk '{print $4}')
if [ "${available_space}" -lt 102400 ]; then
  log_error "Insufficient disk space at ${DESTINATION}. Available: ${available_space}KB (minimum 100MB required)"
  exit 1
fi
log_info "Available disk space: $((available_space / 1024))MB"


# Run azcopy

log_info "Syncing source ${source_stripped} to destination ${DESTINATION}..."

# Capture start time and initial file count for statistics
sync_start_time=$(date +%s)
initial_file_count=$(find "${DESTINATION}" -type f 2>/dev/null | wc -l || echo "0")

azcopy sync --recursive --delete-destination="${DELETE_DESTINATION}" "${SOURCE}" "${DESTINATION}"
result=$?

# Calculate and log statistics
sync_end_time=$(date +%s)
sync_duration=$((sync_end_time - sync_start_time))
final_file_count=$(find "${DESTINATION}" -type f 2>/dev/null | wc -l || echo "0")

log_info "Sync operation completed in ${sync_duration} seconds"
log_info "Files in destination: ${final_file_count} (was ${initial_file_count})"

if [ ${result} -ne 0 ]; then
  log_error "Sync from source ${source_stripped} to destination ${DESTINATION} failed."
  exit ${result}
fi

log_info "Sync completed successfully."
