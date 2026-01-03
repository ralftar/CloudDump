#!/bin/sh

set -e  # Exit immediately if a command fails

awscli_version="2.22.19"
awscli_date="20250116"  # Used for version tracking
awscli_url="https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${awscli_version}.zip"
awscli_filename="awscliv2.zip"

# Install unzip if needed
which unzip >/dev/null 2>&1 || (apt-get update && apt-get install -y unzip)

# Download AWS CLI
curl -f -L -o "/tmp/${awscli_filename}" "${awscli_url}"

# Extract
cd /tmp
unzip -q "${awscli_filename}"
rm "${awscli_filename}"

# Install
/tmp/aws/install

# Cleanup
rm -rf /tmp/aws
