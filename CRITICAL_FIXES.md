# Critical Fixes - Quick Reference

This document lists the most critical issues that should be fixed immediately, with specific line numbers and suggested fixes.

## 1. Remove Unused Code (HIGH PRIORITY)

**Issue:** Helper functions defined but never called - causing maintenance confusion

**Location:** `start.sh` lines 228-360

**Functions affected:**
- `execute_s3bucket_job()` (lines 228-270)
- `execute_azstorage_job()` (lines 272-310)
- `execute_pgsql_job()` (lines 312-360)

**Decision Required:**
Choose ONE of these options:

### Option A: Delete unused functions
Remove lines 228-360 entirely since the main loop already implements job execution inline (lines 844-985).

### Option B: Use the helper functions
Replace the inline job execution code (lines 844-985) with calls to these functions:

```bash
# Replace lines 844-985 with:
if [ "${type}" = "s3bucket" ]; then
  execute_s3bucket_job "${i}" "${jobid}" "${jobdebug}" "${LOGFILE}"
  result=$?
elif [ "${type}" = "azstorage" ]; then
  execute_azstorage_job "${i}" "${jobid}" "${jobdebug}" "${LOGFILE}"
  result=$?
elif [ "${type}" = "pgsql" ]; then
  execute_pgsql_job "${i}" "${jobid}" "${jobdebug}" "${LOGFILE}"
  result=$?
else
  log "Error: Unknown job type ${type} for job ${jobid}." >> "${LOGFILE}"
  result=1
fi
```

**Recommendation:** Option B (use the functions) - it's cleaner and more maintainable.

---

## 2. Fix PostgreSQL Script Interface (HIGH PRIORITY)

**Issue:** Two conflicting interfaces for dump_pgsql.sh

### Problem 1: Unused helper function uses old interface

**Location:** `start.sh` lines 347-350

```bash
# OLD interface (positional parameters) - currently in unused helper
/bin/bash dump_pgsql.sh "${PGHOST}" "${PGPORT}" "${PGUSERNAME}" "${PGPASSWORD}" \
  "${backuppath}" "${filenamedate}" "${compress}" "${databases_json}" "${databases_excluded_json}"
```

**But** the actual script expects:
```bash
# NEW interface (flag-based parameters)
/bin/bash dump_pgsql.sh -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSERNAME}" -P "${PGPASSWORD}" \
  -d "${database}" -b "${backuppath}" -f "${filenamedate}" -z "${compress}" \
  -i "${tables_included}" -x "${tables_excluded}"
```

### Problem 2: Script variables not used

**Location:** `dump_pgsql.sh` lines 57-60, 162-168

Variables defined but not used:
- `TABLES_INCLUDED` (line 57)
- `TABLES_EXCLUDED` (line 60)
- `DATABASES_JSON` (line 162)
- `DATABASES_EXCLUDED_JSON` (line 167)

### Fix Required:

1. **Update helper function** (if keeping them - see issue #1):
   Change lines 347-350 to use the flag-based interface matching the actual script

2. **Remove unused variables from dump_pgsql.sh**:
   - Remove `TABLES_INCLUDED` and `TABLES_EXCLUDED` parameters (lines 56-60)
   - Remove `DATABASES_JSON` and `DATABASES_EXCLUDED_JSON` variables (lines 162-168)
   - Script should handle only ONE database per call (already does this)

3. **Keep iteration logic in start.sh**:
   The main loop already correctly iterates over databases (lines 948-977)

---

## 3. Fix ShellCheck Warnings (HIGH PRIORITY)

### Issue: Exit code checking (SC2181)

**Problem:** Using `$?` indirectly instead of checking command directly

**Bad:**
```bash
jq -r ".jobs[${i}].id" "${CONFIGFILE}"
if [ $? -ne 0 ]; then
  error "Failed"
fi
```

**Good:**
```bash
if ! jobid=$(jq -r ".jobs[${i}].id" "${CONFIGFILE}"); then
  error "Failed"
fi
```

**Locations to fix:**
- Line 47, 121, 133, 135, 209, 381, 446, 451, 474, 478, 499, 506, 518, 585, 590, 598, 791, 795, 801

### Issue: Unquoted variables (SC2086)

**Problem:** Variables not quoted, can cause word splitting

**Bad:**
```bash
echo "${message}" | ${MAIL} -s "Subject" ${attachments} "${MAILTO}"
```

**Good:**
```bash
echo "${message}" | ${MAIL} -s "Subject" "${attachments}" "${MAILTO}"
```

**Locations to fix:**
- Lines 154, 187, 189 (attachments variable)
- Lines 305 (tables_included_params, tables_excluded_params in dump_pgsql.sh)

### Issue: Use parameter expansion instead of sed (SC2001)

**Problem:** Inefficient use of echo | sed

**Bad:**
```bash
text=$(echo "${text}" | sed 's/pattern/replacement/g')
```

**Good:**
```bash
text="${text//pattern/replacement}"
```

**Locations to fix:**
- Lines 65-66 (redaction function)

---

## 4. Add Configuration Validation (HIGH PRIORITY)

**Issue:** No validation of JSON configuration at startup

**Add this function before line 569** (before reading jobs):

```bash
# Validate configuration
validate_config() {
  log "Validating configuration..."
  
  # Check JSON syntax
  if ! jq empty "${CONFIGFILE}" 2>/dev/null; then
    error "Invalid JSON in configuration file ${CONFIGFILE}"
    return 1
  fi
  
  # Check required settings
  local required_settings="HOST SMTPSERVER SMTPPORT MAILFROM MAILTO"
  for setting in ${required_settings}; do
    local value=$(jq -r ".settings.${setting}" "${CONFIGFILE}" | sed 's/^null$//g')
    if [ "${value}" = "" ]; then
      error "Missing required setting: settings.${setting}"
      return 1
    fi
  done
  
  # Check jobs array exists
  local jobs_count=$(jq -r ".jobs | length" "${CONFIGFILE}" 2>/dev/null)
  if [ "${jobs_count}" = "" ] || [ "${jobs_count}" = "null" ]; then
    error "Missing or invalid jobs array in configuration"
    return 1
  fi
  
  # Validate each job
  for ((i = 0; i < jobs_count; i++)); do
    local jobid=$(jq -r ".jobs[${i}].id" "${CONFIGFILE}" 2>/dev/null | sed 's/^null$//g')
    local jobtype=$(jq -r ".jobs[${i}].type" "${CONFIGFILE}" 2>/dev/null | sed 's/^null$//g')
    local crontab=$(jq -r ".jobs[${i}].crontab" "${CONFIGFILE}" 2>/dev/null | sed 's/^null$//g')
    
    if [ "${jobid}" = "" ]; then
      error "Job at index ${i} missing id field"
      return 1
    fi
    
    if [ "${jobtype}" = "" ]; then
      error "Job ${jobid} missing type field"
      return 1
    fi
    
    if [ "${jobtype}" != "s3bucket" ] && [ "${jobtype}" != "azstorage" ] && [ "${jobtype}" != "pgsql" ]; then
      error "Job ${jobid} has invalid type: ${jobtype} (must be s3bucket, azstorage, or pgsql)"
      return 1
    fi
    
    if [ "${crontab}" = "" ]; then
      error "Job ${jobid} missing crontab field"
      return 1
    fi
    
    # Validate cron pattern (basic check)
    local cron_fields=$(echo "${crontab}" | wc -w)
    if [ "${cron_fields}" -ne 5 ]; then
      error "Job ${jobid} has invalid crontab pattern: ${crontab} (must have 5 fields)"
      return 1
    fi
  done
  
  log "Configuration validation passed."
  return 0
}

# Call validation
if ! validate_config; then
  error "Configuration validation failed. Exiting."
  exit 1
fi
```

---

## 5. Security Fixes (HIGH PRIORITY)

### Issue A: SSH key not cleaned up

**Location:** `start.sh` lines 500-504

**Current:**
```bash
if [ ! "${privkey}" = "" ]; then
  mkdir -p "${HOME}/.ssh" || exit 1
  echo "${privkey}" >"${HOME}/.ssh/id_rsa" || exit 1
  chmod 600 "${HOME}/.ssh/id_rsa" || exit 1
fi
```

**Fixed:**
```bash
if [ ! "${privkey}" = "" ]; then
  mkdir -p "${HOME}/.ssh" || exit 1
  # Add cleanup trap
  trap 'rm -f "${HOME}/.ssh/id_rsa"' EXIT
  echo "${privkey}" >"${HOME}/.ssh/id_rsa" || exit 1
  chmod 600 "${HOME}/.ssh/id_rsa" || exit 1
fi
```

### Issue B: SMB credentials not cleaned up

**Location:** `start.sh` lines 534-546

**Current:**
```bash
if [ ! "${username}" = "" ]; then
  mkdir -p /dev/shm || exit 1
  smbcredentials="/dev/shm/.smbcredentials"
  if [ "${password}" = "" ]; then
    echo -e "${username}\n" > "${smbcredentials}"
  else
    echo -e "${username}\n${password}" > "${smbcredentials}"
  fi
  chmod 600 "${smbcredentials}" || exit 1
```

**Fixed:**
```bash
if [ ! "${username}" = "" ]; then
  mkdir -p /dev/shm || exit 1
  smbcredentials="/dev/shm/.smbcredentials"
  # Add cleanup trap
  trap 'rm -f "${smbcredentials}" /dev/shm/smbnetfs.conf' EXIT
  if [ "${password}" = "" ]; then
    echo -e "${username}\n" > "${smbcredentials}"
  else
    echo -e "${username}\n${password}" > "${smbcredentials}"
  fi
  chmod 600 "${smbcredentials}" || exit 1
```

### Issue C: Set proper permissions on credential files

**Location:** `start.sh` lines 434-435

**Current:**
```bash
touch /etc/postfix/relay || exit 1
touch /etc/postfix/sasl_passwd || exit 1
```

**Fixed:**
```bash
touch /etc/postfix/relay || exit 1
chmod 600 /etc/postfix/relay || exit 1
touch /etc/postfix/sasl_passwd || exit 1
chmod 600 /etc/postfix/sasl_passwd || exit 1
```

---

## 6. Improve Lockfile Handling (MEDIUM PRIORITY)

**Issue:** Stale lockfiles not cleaned up

**Location:** `start.sh` lines 824-1008

**Add this function before the main loop:**

```bash
# Clean up stale lockfiles (older than 24 hours)
cleanup_stale_locks() {
  local lockdir="/tmp"
  local max_age_seconds=86400  # 24 hours
  local current_time=$(date +%s)
  
  for lockfile in "${lockdir}"/LOCKFILE_*; do
    if [ -f "${lockfile}" ]; then
      local file_time=$(stat -c %Y "${lockfile}" 2>/dev/null || stat -f %m "${lockfile}" 2>/dev/null)
      if [ -n "${file_time}" ]; then
        local age=$((current_time - file_time))
        if [ "${age}" -gt "${max_age_seconds}" ]; then
          log "Removing stale lockfile: ${lockfile} (age: ${age}s)"
          rm -f "${lockfile}"
        fi
      fi
    fi
  done
}

# Call before starting main loop
cleanup_stale_locks
```

**Add to main loop** (after line 783):

```bash
# Clean up stale locks every hour
if [ $(($(date +%s) % 3600)) -lt 60 ]; then
  cleanup_stale_locks
fi
```

---

## Summary of Actions

| Priority | Issue | Lines Affected | Effort | Impact |
|----------|-------|----------------|--------|--------|
| HIGH | Remove unused code | 228-360, 844-985 | Medium | High (Maintainability) |
| HIGH | Fix PgSQL interface | 347-350, 57-60, 162-168 | Low | High (Correctness) |
| HIGH | Fix ShellCheck warnings | Multiple | Medium | Medium (Quality) |
| HIGH | Add config validation | Before line 569 | Medium | High (Reliability) |
| HIGH | Security fixes | 434-435, 500-504, 534-546 | Low | High (Security) |
| MEDIUM | Improve lockfile handling | 366, 783-1008 | Medium | Medium (Reliability) |

## Recommended Fix Order

1. **Fix ShellCheck warnings** (safest, lowest risk)
2. **Add configuration validation** (catches errors early)
3. **Security fixes** (important but low risk of breaking things)
4. **Fix PostgreSQL interface** (requires coordination between files)
5. **Remove unused code** (requires decision on which approach to take)
6. **Improve lockfile handling** (enhancement, lower priority)

## Testing After Fixes

1. **Syntax check:** `bash -n start.sh dump_*.sh`
2. **ShellCheck:** `shellcheck start.sh dump_*.sh`
3. **Dry run:** Test with valid and invalid configurations
4. **Integration:** Test each job type (s3, azure, pgsql)
5. **Security:** Verify credential files are created with correct permissions
6. **Concurrency:** Test lockfile mechanism with multiple jobs
