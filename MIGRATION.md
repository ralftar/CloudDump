# Migration Guide: Simplified Database Configuration

## Overview

This document explains the changes made to simplify the PostgreSQL database backup configuration pattern in CloudDump.

## What Changed

### Before (Old Pattern)
The old configuration had redundant fields:
```json
{
  "databases": [
    {
      "mydb": {
        "tables_included": [],
        "tables_excluded": ["table1", "table2"]
      }
    }
  ],
  "databases_included": ["mydb"],  // ❌ REDUNDANT
  "databases_excluded": ["template0", "template1"]
}
```

### After (New Pattern)
The new configuration is simpler and clearer:
```json
{
  "databases": [
    {
      "mydb": {
        "tables_included": [],
        "tables_excluded": ["table1", "table2"]
      }
    }
  ],
  "databases_excluded": []  // Not needed when using explicit databases
}
```

## Key Changes

1. **Removed `databases_included` field** - This was redundant since databases listed in the `databases` array are already "included"
2. **Clarified behavior** - Now there are two clear patterns:
   - Pattern A: List specific databases in `databases` array
   - Pattern B: Leave `databases` empty and use `databases_excluded` for exclusions

## Migration Steps

### If you were using `databases_included`:

**Old configuration:**
```json
{
  "databases": [
    {
      "db1": {
        "tables_included": [],
        "tables_excluded": ["logs"]
      }
    },
    {
      "db2": {
        "tables_included": [],
        "tables_excluded": []
      }
    }
  ],
  "databases_included": ["db1", "db2"],
  "databases_excluded": ["template0", "template1"]
}
```

**New configuration:**
```json
{
  "databases": [
    {
      "db1": {
        "tables_included": [],
        "tables_excluded": ["logs"]
      }
    },
    {
      "db2": {
        "tables_included": [],
        "tables_excluded": []
      }
    }
  ],
  "databases_excluded": []
}
```

**What to do:**
1. Remove the `databases_included` array
2. Keep only the databases you want in the `databases` array
3. Set `databases_excluded` to an empty array (or omit it)

### If you were backing up all databases with exclusions:

**Old configuration:**
```json
{
  "databases": [],
  "databases_included": [],
  "databases_excluded": ["template0", "template1", "postgres"]
}
```

**New configuration:**
```json
{
  "databases": [],
  "databases_excluded": ["template0", "template1", "postgres"]
}
```

**What to do:**
1. Remove the `databases_included` array (it was always empty in this case anyway)
2. Keep `databases` as an empty array
3. Keep your `databases_excluded` array as-is

## Behavior Reference

### Pattern A: Specific Databases
```json
{
  "databases": [
    {"production": {...}},
    {"analytics": {...}}
  ],
  "databases_excluded": []
}
```
- ✅ Backs up: `production`, `analytics`
- ❌ Ignores: All other databases
- ℹ️ Note: `databases_excluded` is ignored when `databases` is not empty

### Pattern B: All Databases with Exclusions
```json
{
  "databases": [],
  "databases_excluded": ["template0", "template1", "postgres"]
}
```
- ✅ Backs up: All databases except those excluded
- ❌ Ignores: `template0`, `template1`, `postgres`

## Benefits

1. **Simpler** - One less field to configure
2. **Clearer** - No ambiguity about what gets backed up
3. **Less redundant** - No need to list databases in two places
4. **Easier to understand** - Two clear patterns instead of complex combinations
5. **Fewer lines of code** - Removed ~50 lines from pgdump.sh

## No Breaking Changes for Tables

Table filtering remains unchanged:
- `tables_included`: Backup only these tables
- `tables_excluded`: Backup all tables except these

## Questions?

See the [config.example.json](../config.example.json) file for complete examples of both patterns.
