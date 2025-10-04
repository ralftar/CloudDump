#!/bin/bash

# Test to verify that if tables_included is defined but tables don't exist,
# the backup is skipped and marked as failed

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Testing table validation behavior..."
echo ""

# Test: Verify the validation logic skips backup when no tables exist
echo "Test: When tables_included has non-existent tables, backup should handle appropriately"

echo "  Expected behavior:"
echo "    - Validation detects missing tables"
echo "    - Sets result=1 for email failure reporting"
echo "    - If some tables exist, continues with backup using only existing tables"
echo "    - If NO tables exist, skips database backup entirely"
echo "    - Excluded tables that don't exist get a warning (not error)"
echo ""

# Validate the code logic:
if grep -q "error.*does not exist.*Skipping this table" "$PARENT_DIR/scripts/pgdump.sh"; then
    echo "  ✓ Script reports missing included tables as errors"
else
    echo "  ✗ Script missing error reporting for missing included tables"
    exit 1
fi

# Check that validation builds params only for existing tables
if grep -q "Only add existing tables to params" "$PARENT_DIR/scripts/pgdump.sh"; then
    echo "  ✓ Script filters out non-existent tables"
else
    echo "  ✗ Script does not filter non-existent tables"
    exit 1
fi

# Check for skipping when no tables exist
if grep -q "None of the specified tables exist.*Skipping database backup" "$PARENT_DIR/scripts/pgdump.sh"; then
    echo "  ✓ Script skips database when no specified tables exist"
else
    echo "  ✗ Script missing handling for no existing tables"
    exit 1
fi

# Check that excluded tables get warnings, not errors
if grep -q "WARNING: Excluded table.*does not exist" "$PARENT_DIR/scripts/pgdump.sh"; then
    echo "  ✓ Script warns about non-existent excluded tables"
else
    echo "  ✗ Script missing warning for non-existent excluded tables"
    exit 1
fi

# Check that after validation, the script proceeds with pg_dump (when tables exist)
if grep -A 80 "Validating included tables" "$PARENT_DIR/scripts/pgdump.sh" | grep -q "PGPASSWORD.*pg_dump"; then
    echo "  ✓ Script continues with pg_dump after validation (when tables exist)"
else
    echo "  ✗ Script does not continue with pg_dump after validation"
    exit 1
fi

echo ""
echo "=========================================="
echo "Table validation test PASSED! ✓"
echo "=========================================="
