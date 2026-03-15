# Security Policy

## Reporting a vulnerability

Report security issues via GitHub's private vulnerability reporting:

**[Report a vulnerability](../../security/advisories/new)**

Do **not** open a public issue for security vulnerabilities.

## Response

I aim to acknowledge reports within 48 hours and provide a fix or
mitigation within 7 days for critical issues.

## Scope

CloudDump handles sensitive credentials (AWS keys, database passwords,
API tokens). The following are in scope:

- Credential leakage in logs, emails, or process arguments
- Path traversal in backup destinations
- Command injection via configuration values
- Container escape or privilege escalation
- Redaction bypasses

## Known limitations

These are by design and not considered vulnerabilities:

- **AWS CLI v1** is used (Debian 12 apt). v2 is not available via apt
  for this release.
- **Database credentials** are passed via environment variables to
  pg_dump/mysqldump. This is standard practice for these tools.
- **GitHub token** is written to a temporary file (deleted after use)
  because `github-backup` does not support environment variables.
- The container requires write access to `/backup`. Use appropriate
  volume permissions in your orchestrator.
