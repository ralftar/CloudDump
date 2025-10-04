#!/bin/bash

# Test script to validate bash syntax of all shell scripts

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Validating bash syntax for all scripts..."
echo ""

FAILED=0

for script in "$PARENT_DIR"/scripts/*.sh "$PARENT_DIR"/tools/*.sh; do
  if [ -f "$script" ]; then
    filename=$(basename "$script")
    echo -n "Checking $filename... "
    if bash -n "$script" 2>&1; then
      echo "✓ PASSED"
    else
      echo "✗ FAILED"
      FAILED=1
    fi
  fi
done

echo ""
if [ $FAILED -eq 0 ]; then
  echo "=========================================="
  echo "All syntax checks PASSED! ✓"
  echo "=========================================="
  exit 0
else
  echo "=========================================="
  echo "Some syntax checks FAILED! ✗"
  echo "=========================================="
  exit 1
fi
