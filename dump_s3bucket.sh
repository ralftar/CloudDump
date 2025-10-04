#!/bin/bash

# Vendanor S3Dump Script
# This script runs aws s3 sync
# Usage:
#   dump_s3bucket.sh [-s source] [-d destination] [-D delete_destination]
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
DELETE_DESTINATION="false"
AWS_ACCESS_KEY_ID_PARAM=""
AWS_SECRET_ACCESS_KEY_PARAM=""
AWS_REGION_PARAM="us-east-1"
ENDPOINT_URL=""

# ----------------------------
# Parse command-line arguments
# ----------------------------
while getopts "s:d:D:a:k:r:e:" opt; do
  case ${opt} in
    s )
      SOURCE="${OPTARG}"
      ;;
    d )
      DESTINATION="${OPTARG}"
      ;;
    D )
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

print "Vendanor S3Dump ($0)"


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
  error "Missing \"${cmds_missing}\" commands."
  exit 1
fi


# Check parameters

if [ "${SOURCE}" = "" ]; then
  error "Missing source parameter (-s)."
  exit 1
fi

if [ "${DESTINATION}" = "" ]; then
  error "Missing destination parameter (-d)."
  exit 1
fi

# Ensure delete_destination is boolean
if [ "${DELETE_DESTINATION}" != "true" ] && [ "${DELETE_DESTINATION}" != "false" ]; then
  DELETE_DESTINATION="false"
fi

print "Source: ${SOURCE}"
print "Destination: ${DESTINATION}"
print "Delete destination: ${DELETE_DESTINATION}"
print "AWS Region: ${AWS_REGION_PARAM}"
if [ ! "${ENDPOINT_URL}" = "" ]; then
  print "Endpoint URL: ${ENDPOINT_URL}"
fi


# Validate source

echo "${SOURCE}" | grep "^s3:\/\/.*" >/dev/null 2>&1
if [ $? -ne 0 ]; then
  error "Invalid source. Source must start with s3://"
  exit 1
fi


# Create directory

print "Creating directory for destination ${DESTINATION}"

mkdir -p "${DESTINATION}"
if [ $? -ne 0 ]; then
  error "Could not create directory ${DESTINATION}"
  exit 1
fi


# Check permissions

print "Checking permission for destination ${DESTINATION}"

touch "${DESTINATION}/TEST_FILE"
if [ $? -ne 0 ]; then
  error "Could not access ${DESTINATION}."
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

print "Syncing source ${SOURCE} to destination ${DESTINATION}..."

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
  error "Sync from source ${SOURCE} to destination ${DESTINATION} failed."
  exit ${result}
fi

print "Sync completed successfully."
