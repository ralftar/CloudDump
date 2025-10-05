#!/bin/bash

# Vendanor S3Dump Script
# This script runs aws s3 sync
# Usage:
#   dump_s3bucket.sh [-s source] [-d destination] [-m mirror]
#                    [-a aws_access_key_id] [-k aws_secret_access_key]
#                    [-r aws_region] [-e endpoint_url]
#
# Example:
#   dump_s3bucket.sh -s s3://my-bucket/path -d /backups/s3 -a AKIAIOSFODNN7EXAMPLE -k wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY -r us-east-1

# ----------------------------
# Default values
# ----------------------------
SOURCE=""
DESTINATION=""
DELETE_DESTINATION="true"
AWS_ACCESS_KEY_ID_PARAM=""
AWS_SECRET_ACCESS_KEY_PARAM=""
AWS_REGION_PARAM="us-east-1"
ENDPOINT_URL=""

# ----------------------------
# Parse command-line arguments
# ----------------------------
while getopts "s:d:m:a:k:r:e:" opt; do
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
    a )
      AWS_ACCESS_KEY_ID_PARAM="${OPTARG}"
      ;;
    k )
      AWS_SECRET_ACCESS_KEY_PARAM="${OPTARG}"
      ;;
    r )
      AWS_REGION_PARAM="${OPTARG}"
      ;;
    e )
      ENDPOINT_URL="${OPTARG}"
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

log_info "Vendanor S3Dump ($0)"


# Check commands

cmds="which sed date touch mkdir rm aws"
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

log_info "Source: ${SOURCE}"
log_info "Destination: ${DESTINATION}"
log_info "Mirror (delete): ${DELETE_DESTINATION}"
log_info "AWS Region: ${AWS_REGION_PARAM}"
if [ ! "${ENDPOINT_URL}" = "" ]; then
  log_info "Endpoint URL: ${ENDPOINT_URL}"
fi


# Validate source

echo "${SOURCE}" | grep "^s3:\/\/.*" >/dev/null 2>&1
if [ $? -ne 0 ]; then
  log_error "Invalid source. Source must start with s3://"
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


# Set AWS credentials
if [ ! "${AWS_ACCESS_KEY_ID_PARAM}" = "" ]; then
  export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID_PARAM}"
fi
if [ ! "${AWS_SECRET_ACCESS_KEY_PARAM}" = "" ]; then
  export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY_PARAM}"
fi
if [ ! "${AWS_REGION_PARAM}" = "" ]; then
  export AWS_DEFAULT_REGION="${AWS_REGION_PARAM}"
fi


# Run aws s3 sync

log_info "Syncing source ${SOURCE} to destination ${DESTINATION}..."

aws_cmd="aws s3 sync"

# Add endpoint URL for MinIO compatibility
if [ ! "${ENDPOINT_URL}" = "" ]; then
  aws_cmd="${aws_cmd} --endpoint-url \"${ENDPOINT_URL}\""
fi

# Add delete flag if needed
if [ "${DELETE_DESTINATION}" = "true" ]; then
  aws_cmd="${aws_cmd} --delete"
fi

# Add source and destination
aws_cmd="${aws_cmd} \"${SOURCE}\" \"${DESTINATION}\""

# Execute the command
eval "${aws_cmd}"
result=$?

# Unset AWS credentials
unset AWS_ACCESS_KEY_ID
unset AWS_SECRET_ACCESS_KEY
unset AWS_DEFAULT_REGION

if [ ${result} -ne 0 ]; then
  log_error "Sync from source ${SOURCE} to destination ${DESTINATION} failed."
  exit ${result}
fi

log_info "Sync completed successfully."
