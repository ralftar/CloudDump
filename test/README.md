# Test Suite for CloudDump

This directory contains tests for the CloudDump project.

## Running Tests

### All Tests
```bash
./test/test-syntax.sh && ./test/test-config-parsing.sh
```

### Individual Tests

#### Syntax Validation
Tests that all shell scripts have valid bash syntax:
```bash
./test/test-syntax.sh
```

#### Configuration Parsing
Tests the simplified database includes/excludes logic:
```bash
./test/test-config-parsing.sh
```

## Test Coverage

- **test-syntax.sh**: Validates bash syntax for all `.sh` files
- **test-config-parsing.sh**: Validates JSON configuration parsing logic for the simplified database selection pattern

## Notes

These tests do not require a live PostgreSQL database or Azure storage account. They validate:
- Script syntax correctness
- Configuration file parsing
- JSON validity
- Logic correctness for database selection patterns
