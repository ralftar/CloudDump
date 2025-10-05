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

# Generates a formatted timestamp string for logging purposes
#
# Returns:
#   Current date and time in 'YYYY-MM-DD HH:MM:SS' format
#
generates_timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

# Writes an informational message to stdout with timestamp prefix
#
# Arguments:
#   All arguments are concatenated and logged as the message
#
# Output:
#   [YYYY-MM-DD HH:MM:SS] message
#
writes_info_message() {
  echo "[$(generates_timestamp)] $*"
}

# Writes an writes_error_message message to stderr with timestamp and ERROR prefix
#
# Arguments:
#   All arguments are concatenated and logged as the writes_error_message message
#
# Output:
#   [YYYY-MM-DD HH:MM:SS] ERROR: message (sent to stderr)
#
writes_error_to_stderr() {
  echo "[$(generates_timestamp)] ERROR: $*" >&2
}

# Writes an writes_error_message message (wrapper for writes_error_to_stderr)
#
# Arguments:
#   All arguments are passed to writes_error_to_stderr
#
writes_error_message() {
  writes_error_to_stderr "$@"
}


# Init

writes_info_message "Vendanor AzDump ($0)"


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
  writes_error_message "Missing \"${cmds_missing}\" commands."
  exit 1
fi


# Check parameters

if [ "${SOURCE}" = "" ]; then
  writes_error_message "Missing source parameter (-s)."
  exit 1
fi

if [ "${DESTINATION}" = "" ]; then
  writes_error_message "Missing destination parameter (-d)."
  exit 1
fi

# Ensure delete_destination is boolean
if [ "${DELETE_DESTINATION}" != "true" ] && [ "${DELETE_DESTINATION}" != "false" ]; then
  DELETE_DESTINATION="true"
fi

source_stripped=$(echo "${SOURCE}" | cut -d '?' -f 1)

writes_info_message "Source: ${source_stripped}"
writes_info_message "Destination: ${DESTINATION}"
writes_info_message "Mirror (delete): ${DELETE_DESTINATION}"


# Validate source

echo "${SOURCE}" | grep "^https:\/\/.*" >/dev/null 2>&1
if [ $? -ne 0 ]; then
  writes_error_message "Invalid source. Source must start with https://"
  exit 1
fi


# Create directory

writes_info_message "Creating directory for destination ${DESTINATION}"

mkdir -p "${DESTINATION}"
if [ $? -ne 0 ]; then
  writes_error_message "Could not create directory ${DESTINATION}"
  exit 1
fi


# Check permissions

writes_info_message "Checking permission for destination ${DESTINATION}"

touch "${DESTINATION}/TEST_FILE"
if [ $? -ne 0 ]; then
  writes_error_message "Could not access ${DESTINATION}."
  exit 1
fi

rm -f "${DESTINATION}/TEST_FILE"


# Run azcopy

writes_info_message "Syncing source ${source_stripped} to destination ${DESTINATION}..."

azcopy sync --recursive --delete-destination="${DELETE_DESTINATION}" "${SOURCE}" "${DESTINATION}"
result=$?

if [ ${result} -ne 0 ]; then
  writes_error_message "Sync from source ${source_stripped} to destination ${DESTINATION} failed."
  exit ${result}
fi

writes_info_message "Sync completed successfully."
