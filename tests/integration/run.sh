#!/usr/bin/env bash
# Integration test: build CloudDump, start fakes, seed, run, verify, teardown.
# Everything is containerised — only Docker is required on the host.
#
# Usage:
#   bash tests/integration/run.sh            # normal run (cleanup on exit)
#   bash tests/integration/run.sh --keep     # keep containers running after test
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE="docker compose -f $SCRIPT_DIR/docker-compose.yml"
BACKUP_DIR="$SCRIPT_DIR/output"
CONTAINER="clouddump-integration-test"
KEEP=false
PASSED=0
FAILED=0

[[ "${1:-}" == "--keep" ]] && KEEP=true

# ── Helpers ──────────────────────────────────────────────────────────────────

cleanup() {
    echo ""
    if $KEEP; then
        echo "Keeping containers running (--keep).  Tear down manually with:"
        echo "  docker rm -f $CONTAINER"
        echo "  $COMPOSE down -v"
        echo "  rm -rf $BACKUP_DIR"
    else
        echo "Cleaning up..."
        docker rm -f "$CONTAINER" 2>/dev/null || true
        $COMPOSE down -v 2>/dev/null || true
        docker run --rm -v "$BACKUP_DIR:/cleanup" alpine rm -rf /cleanup 2>/dev/null || true
        rm -rf "$BACKUP_DIR" 2>/dev/null || true
    fi
}
trap cleanup EXIT

check() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  PASS  $desc"
        PASSED=$((PASSED + 1))
    else
        echo "  FAIL  $desc"
        FAILED=$((FAILED + 1))
    fi
}

# ── Preflight ────────────────────────────────────────────────────────────────

for cmd in docker python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: $cmd is required but not found." >&2
        exit 1
    fi
done

echo "=== CloudDump Integration Test ==="
echo ""

# ── 1. Build ─────────────────────────────────────────────────────────────────

echo "[1/6] Building CloudDump image..."
docker build -q -t clouddump:integration-test "$REPO_DIR" >/dev/null
echo "  Image built."

# ── 2. Start fakes ──────────────────────────────────────────────────────────

echo "[2/6] Starting fake services (MinIO, PostgreSQL, MySQL, Mailpit)..."
$COMPOSE up -d --wait
echo "  All services healthy."

# ── 3. Seed ──────────────────────────────────────────────────────────────────

echo "[3/6] Seeding test data..."
bash "$SCRIPT_DIR/seed.sh"

# ── 4. Run CloudDump ────────────────────────────────────────────────────────

echo "[4/6] Starting CloudDump container..."
rm -rf "$BACKUP_DIR"
mkdir -m 777 -p "$BACKUP_DIR"

docker run -d --name "$CONTAINER" \
    --network clouddump-integration \
    -v "$SCRIPT_DIR/config.json:/config/config.json:ro" \
    -v "$BACKUP_DIR:/backup" \
    clouddump:integration-test >/dev/null

echo "  Container started."

# ── 5. Wait & verify ────────────────────────────────────────────────────────

echo "[5/6] Waiting for jobs to complete (polling every 5s, up to 150s)..."
DONE=false
for i in $(seq 1 30); do
    if ! docker inspect "$CONTAINER" --format='{{.State.Running}}' 2>/dev/null | grep -q true; then
        echo "  WARNING: container exited early."
        break
    fi

    # S3 local + pgsql + mysql all finished?
    if [ -f "$BACKUP_DIR/s3/file1.txt" ] \
        && compgen -G "$BACKUP_DIR/pgsql/"*.bz2 >/dev/null 2>&1 \
        && compgen -G "$BACKUP_DIR/mysql/"*.bz2 >/dev/null 2>&1; then
        echo "  All jobs finished after ~$((i * 5))s."
        sleep 3
        DONE=true
        break
    fi
    sleep 5
done

if ! $DONE; then
    echo "  WARNING: timed out — some jobs may not have completed."
fi

# Print CloudDump logs
echo ""
echo "--- CloudDump logs (last 60 lines) ---"
docker logs "$CONTAINER" 2>&1 | tail -60
echo "--- end logs ---"
echo ""

# ── Verification ─────────────────────────────────────────────────────────────

echo "=== Verification ==="
echo ""

echo "  Container:"
check "CloudDump is still running" \
    docker inspect "$CONTAINER" --format='{{.State.Running}}' 2>/dev/null

echo ""
echo "  S3 sync:"
check "file1.txt exists"                test -f "$BACKUP_DIR/s3/file1.txt"
check "file1.txt has expected content"  grep -q "file1" "$BACKUP_DIR/s3/file1.txt"
check "subdir/file2.txt exists"         test -f "$BACKUP_DIR/s3/subdir/file2.txt"
check "subdir/nested/file3.txt exists"  test -f "$BACKUP_DIR/s3/subdir/nested/file3.txt"

echo ""
echo "  PostgreSQL dump:"
check "testuser dump exists and non-empty"  test -s "$BACKUP_DIR/pgsql/testuser.dump.bz2"
check "testdb1 dump exists and non-empty"   test -s "$BACKUP_DIR/pgsql/testdb1.dump.bz2"
check "testdb2 dump exists and non-empty"   test -s "$BACKUP_DIR/pgsql/testdb2.dump.bz2"

echo ""
echo "  MySQL dump:"
check "testdb1 dump exists and non-empty"  test -s "$BACKUP_DIR/mysql/testdb1.sql.bz2"
check "testdb2 dump exists and non-empty"  test -s "$BACKUP_DIR/mysql/testdb2.sql.bz2"

echo ""
echo "  Email (SMTP via Mailpit):"
MAIL_COUNT=$(curl -sf http://localhost:8025/api/v1/messages 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo 0)
check "at least one email received by Mailpit"  test "$MAIL_COUNT" -gt 0

# ── 6. Tool smoke tests ─────────────────────────────────────────────────────

echo ""
echo "[6/6] Running tool smoke tests..."
echo ""

echo "  Bundled tools (in Docker image):"
check "github-backup installed"  docker exec "$CONTAINER" github-backup --help
check "git installed"            docker exec "$CONTAINER" git --version
check "aws CLI installed"        docker exec "$CONTAINER" aws --version
check "azcopy installed"         docker exec "$CONTAINER" azcopy --version
check "pg_dump installed"        docker exec "$CONTAINER" pg_dump --version
check "psql installed"           docker exec "$CONTAINER" psql --version

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "=== Results: $PASSED passed, $FAILED failed ==="

if [ "$FAILED" -eq 0 ]; then
    echo "All checks passed."
    exit 0
else
    echo "Some checks failed — review the logs above."
    exit 1
fi
