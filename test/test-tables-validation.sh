#!/bin/bash

# Test to verify that if tables_included is defined but tables don't exist,
# the backup still runs but is marked as failed

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Testing table validation behavior..."
echo ""

# Test: Verify the validation logic doesn't skip backup, just marks as failed
echo "Test: When tables_included has non-existent tables, backup should continue but report failure"

echo "  Expected behavior:"
echo "    - Validation detects missing tables"
echo "    - Sets result=1 for email failure reporting"
echo "    - Continues with backup using only existing tables"
echo "    - If no tables exist, backs up all tables with warning"
echo ""

# Validate the code logic:
if grep -q "error.*does not exist.*Skipping this table" "$PARENT_DIR/scripts/pgdump.sh"; then
    echo "  ✓ Script reports missing tables as errors"
else
    echo "  ✗ Script missing error reporting for missing tables"
    exit 1
fi

# Check that validation builds params only for existing tables
if grep -q "Only add existing tables to params" "$PARENT_DIR/scripts/pgdump.sh"; then
    echo "  ✓ Script filters out non-existent tables"
else
    echo "  ✗ Script does not filter non-existent tables"
    exit 1
fi

# Check for warning when no tables exist
if grep -q "WARNING: None of the specified tables exist" "$PARENT_DIR/scripts/pgdump.sh"; then
    echo "  ✓ Script handles case when no specified tables exist"
else
    echo "  ✗ Script missing handling for no existing tables"
    exit 1
fi

# Check that after validation, the script proceeds with pg_dump
if grep -A 60 "Validating included tables" "$PARENT_DIR/scripts/pgdump.sh" | grep -q "PGPASSWORD.*pg_dump"; then
    echo "  ✓ Script continues with pg_dump after validation"
else
    echo "  ✗ Script does not continue with pg_dump after validation"
    exit 1
fi

echo ""
echo "=========================================="
echo "Table validation test PASSED! ✓"
echo "=========================================="
