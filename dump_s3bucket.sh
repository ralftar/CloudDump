#!/bin/bash

# Vendanor S3Dump Script
# This script runs aws s3 sync
# Usage: dump_s3bucket.sh <source> <destination> <delete_destination> <aws_access_key_id> <aws_secret_access_key> <aws_region> <endpoint_url>


# Parameters
SOURCE="${1}"
DESTINATION="${2}"
DELETE_DESTINATION="${3}"
AWS_ACCESS_KEY_ID_PARAM="${4}"
AWS_SECRET_ACCESS_KEY_PARAM="${5}"
AWS_REGION_PARAM="${6}"
ENDPOINT_URL="${7}"


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
  error "Missing source parameter."
  exit 1
fi

if [ "${DESTINATION}" = "" ]; then
  error "Missing destination parameter."
  exit 1
fi

if [ "${DELETE_DESTINATION}" = "" ]; then
  DELETE_DESTINATION="false"
fi

if [ "${AWS_REGION_PARAM}" = "" ]; then
  AWS_REGION_PARAM="us-east-1"
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
