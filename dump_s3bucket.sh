#!/bin/bash

# Vendanor S3Dump Script
# This script runs aws s3 sync
# Usage: dump_s3bucket.sh <jobid>


CONFIGFILE="/config/config.json"

JOBID="${1}"


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

cmds="which sed date touch mkdir cp rm jq aws"
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

if [ "${JOBID}" = "" ]; then
  error "Missing Job ID."
  exit 1
fi


# Check configfile

if [ ! -f "${CONFIGFILE}" ]; then
  error "Missing Json configuration file ${CONFIGFILE}."
  exit 1
fi

if [ ! -r "${CONFIGFILE}" ]; then
  error "Can't read Json configuration file ${CONFIGFILE}."
  exit 1
fi


# Find the job index for this job ID

jobs=$(jq -r ".jobs | length" "${CONFIGFILE}")
if [ "${jobs}" = "" ] || [ -z "${jobs}" ] || ! [ "${jobs}" -eq "${jobs}" ] 2>/dev/null; then
  error "Can't read jobs from Json configuration."
  exit 1
fi

job_idx=
for ((i = 0; i < jobs; i++)); do
  jobid_current=$(jq -r ".jobs[${i}].id" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ] || [ "${jobid_current}" = "" ]; then
    continue
  fi
  if [ "${jobid_current}" = "${JOBID}" ]; then
    job_idx="${i}"
    break
  fi
done

if [ "${job_idx}" = "" ]; then
  error "No job ID ${JOBID} in Json configuration."
  exit 1
fi


# Backup each S3 bucket

result=0

bucket_count=$(jq -r ".jobs[${job_idx}].buckets | length" "${CONFIGFILE}")
if [ "${bucket_count}" = "" ] || [ -z "${bucket_count}" ] || ! [ "${bucket_count}" -eq "${bucket_count}" ] 2>/dev/null; then
  error "Can't read buckets from Json configuration."
  exit 1
fi

if [ "${bucket_count}" -eq 0 ]; then
  error "No buckets for ${JOBID} in Json configuration."
  exit 1
fi


for ((bucket_idx = 0; bucket_idx < bucket_count; bucket_idx++)); do

  source=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].source" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ] || [ "${source}" = "" ]; then
    error "Missing source for job index ${job_idx} ID ${JOBID}."
    result=1
    continue
  fi

  destination=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].destination" "${CONFIGFILE}" | sed 's/^null$//g')
  if [ $? -ne 0 ] || [ "${destination}" = "" ]; then
    error "Missing destination for job index ${job_idx} ID ${JOBID}."
    result=1
    continue
  fi

  delete_destination=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].delete_destination" "${CONFIGFILE}" | sed 's/^null$//g')

  if [ "${delete_destination}" = "" ]; then
    delete_destination="false"
  fi

  # Get AWS configuration
  aws_access_key_id=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].aws_access_key_id" "${CONFIGFILE}" | sed 's/^null$//g')
  aws_secret_access_key=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].aws_secret_access_key" "${CONFIGFILE}" | sed 's/^null$//g')
  aws_region=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].aws_region" "${CONFIGFILE}" | sed 's/^null$//g')
  endpoint_url=$(jq -r ".jobs[${job_idx}].buckets[${bucket_idx}].endpoint_url" "${CONFIGFILE}" | sed 's/^null$//g')

  # Set default region if not provided
  if [ "${aws_region}" = "" ]; then
    aws_region="us-east-1"
  fi

  print "Source: ${source}"
  print "Destination: ${destination}"
  print "Delete destination: ${delete_destination}"
  print "AWS Region: ${aws_region}"
  if [ ! "${endpoint_url}" = "" ]; then
    print "Endpoint URL: ${endpoint_url}"
  fi


  # Validate source and destination

  echo "${source}" | grep "^s3:\/\/.*" >/dev/null 2>&1
  if [ $? -ne 0 ]; then
    error "Invalid source for job index ${job_idx} ID ${JOBID}. Source must start with s3://"
    result=1
    continue
  fi


  # Create directory

  print "Creating directory for destination ${destination}"

  mkdir -p "${destination}"
  if [ $? -ne 0 ]; then
    error "Could not create directory ${destination}"
    result=1
    continue
  fi


  # Check permissions

  print "Checking permission for destination ${destination}"

  touch "${destination}/TEST_FILE"
  if [ $? -ne 0 ]; then
    error "Could not access ${destination} for job index ${job_idx} ID ${JOBID}."
    result=1
    continue
  fi

  rm -f "${destination}/TEST_FILE"


  # Set AWS credentials for this bucket
  if [ ! "${aws_access_key_id}" = "" ]; then
    export AWS_ACCESS_KEY_ID="${aws_access_key_id}"
  fi
  if [ ! "${aws_secret_access_key}" = "" ]; then
    export AWS_SECRET_ACCESS_KEY="${aws_secret_access_key}"
  fi
  if [ ! "${aws_region}" = "" ]; then
    export AWS_DEFAULT_REGION="${aws_region}"
  fi


  # Run aws s3 sync

  print "Syncing source ${source} to destination ${destination}..."

  aws_cmd="aws s3 sync"
  
  # Add endpoint URL for MinIO compatibility
  if [ ! "${endpoint_url}" = "" ]; then
    aws_cmd="${aws_cmd} --endpoint-url \"${endpoint_url}\""
  fi

  # Add delete flag if needed
  if [ "${delete_destination}" = "true" ]; then
    aws_cmd="${aws_cmd} --delete"
  fi

  # Add source and destination
  aws_cmd="${aws_cmd} \"${source}\" \"${destination}\""

  # Execute the command
  eval "${aws_cmd}"
  if [ ${?} -ne 0 ]; then
    error "Sync from source ${source} to destination ${destination} failed for job index ${job_idx} ID ${JOBID}."
    result=1
  fi

  # Unset AWS credentials
  unset AWS_ACCESS_KEY_ID
  unset AWS_SECRET_ACCESS_KEY
  unset AWS_DEFAULT_REGION

done


if ! [ "${result}" = "" ]; then
  exit "${result}"
fi
