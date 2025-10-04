#!/bin/bash

# Test script to verify the simplified database includes/excludes logic
# This tests the configuration parsing without needing a real PostgreSQL database

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Testing simplified pgdump configuration logic..."

# Test 1: Verify json parsing for specific databases
echo ""
echo "Test 1: Parsing specific databases configuration..."
CONFIG_FILE="/tmp/test-config.json"

cat > "$CONFIG_FILE" << 'EOF'
{
  "jobs": [
    {
      "id": "test1",
      "servers": [
        {
          "databases": [
            {"production": {}},
            {"analytics": {}}
          ],
          "databases_excluded": []
        }
      ]
    }
  ]
}
EOF

databases_configured=$(jq -r ".jobs[0].servers[0].databases[] | keys[]" "$CONFIG_FILE" 2>/dev/null | tr '\n' ' ')
databases_excluded=$(jq -r ".jobs[0].servers[0].databases_excluded[]" "$CONFIG_FILE" 2>/dev/null | tr '\n' ' ')

echo "  Configured databases: '$databases_configured'"
echo "  Excluded databases: '$databases_excluded'"

if [ "$databases_configured" = "production analytics " ]; then
  echo "  ✓ Test 1 PASSED"
else
  echo "  ✗ Test 1 FAILED: Expected 'production analytics ', got '$databases_configured'"
  exit 1
fi

# Test 2: Verify json parsing for all databases with exclusions
echo ""
echo "Test 2: Parsing all databases with exclusions..."

cat > "$CONFIG_FILE" << 'EOF'
{
  "jobs": [
    {
      "id": "test2",
      "servers": [
        {
          "databases": [],
          "databases_excluded": ["template0", "template1", "postgres"]
        }
      ]
    }
  ]
}
EOF

databases_configured=$(jq -r ".jobs[0].servers[0].databases[] | keys[]" "$CONFIG_FILE" 2>/dev/null | tr '\n' ' ')
databases_excluded=$(jq -r ".jobs[0].servers[0].databases_excluded[]" "$CONFIG_FILE" 2>/dev/null | tr '\n' ' ')

echo "  Configured databases: '$databases_configured'"
echo "  Excluded databases: '$databases_excluded'"

if [ "$databases_configured" = "" ] && [ "$databases_excluded" = "template0 template1 postgres " ]; then
  echo "  ✓ Test 2 PASSED"
else
  echo "  ✗ Test 2 FAILED"
  exit 1
fi

# Test 3: Verify the example config is valid
echo ""
echo "Test 3: Validating config.example.json..."

if [ -f "$PARENT_DIR/config.example.json" ]; then
  if jq . "$PARENT_DIR/config.example.json" > /dev/null 2>&1; then
    echo "  ✓ Test 3 PASSED: config.example.json is valid JSON"
  else
    echo "  ✗ Test 3 FAILED: config.example.json is not valid JSON"
    exit 1
  fi
else
  echo "  ✗ Test 3 FAILED: config.example.json not found"
  exit 1
fi

# Test 4: Verify example has two different job patterns
echo ""
echo "Test 4: Verifying example config has both patterns..."

job1_dbs=$(jq -r '.jobs[] | select(.id == "pgdump-specific-databases") | .servers[0].databases | length' "$PARENT_DIR/config.example.json" 2>/dev/null)
job2_dbs=$(jq -r '.jobs[] | select(.id == "pgdump-all-databases") | .servers[0].databases | length' "$PARENT_DIR/config.example.json" 2>/dev/null)

echo "  Job 1 (specific databases) has $job1_dbs databases configured"
echo "  Job 2 (all databases) has $job2_dbs databases configured"

if [ "$job1_dbs" = "3" ] && [ "$job2_dbs" = "0" ]; then
  echo "  ✓ Test 4 PASSED"
else
  echo "  ✗ Test 4 FAILED: Expected job1=3, job2=0, got job1=$job1_dbs, job2=$job2_dbs"
  exit 1
fi

# Clean up
rm -f "$CONFIG_FILE"

echo ""
echo "=========================================="
echo "All tests PASSED! ✓"
echo "=========================================="
