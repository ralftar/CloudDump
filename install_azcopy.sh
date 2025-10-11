#!/bin/sh

set -e  # Exit immediately if a command fails

azcopy_version="10.29.1"
azcopy_date="20250109"  # Used for version tracking by the azcopy-update workflow
azcopy_url="https://github.com/Azure/azure-storage-azcopy/releases/download/v${azcopy_version}/azcopy_linux_amd64_${azcopy_version}.tar.gz"
azcopy_filename="azcopy.tar.gz"

# Download AzCopy
curl -f -L -o "/tmp/${azcopy_filename}" "${azcopy_url}"

# Extract
tar -C /tmp -xf "/tmp/${azcopy_filename}"
rm "/tmp/${azcopy_filename}"

# Move AzCopy binary to /usr/bin
mv /tmp/azcopy_linux_*/azcopy /usr/bin/azcopy

# Cleanup
rm -rf /tmp/azcopy_linux_*
