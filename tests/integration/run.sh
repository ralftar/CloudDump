#!/usr/bin/env bash
# Integration test: build CloudDump, start fakes, seed, run, verify, teardown.
# Everything is containerised — only Docker and ssh-keygen are required on the host.
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
        echo "  rm -rf $BACKUP_DIR $SCRIPT_DIR/test-keys $SCRIPT_DIR/config.runtime.json"
    else
        echo "Cleaning up..."
        docker rm -f "$CONTAINER" 2>/dev/null || true
        $COMPOSE down -v 2>/dev/null || true
        rm -rf "$BACKUP_DIR" "$SCRIPT_DIR/test-keys" "$SCRIPT_DIR/config.runtime.json"
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

for cmd in docker ssh-keygen python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: $cmd is required but not found." >&2
        exit 1
    fi
done

echo "=== CloudDump Integration Test ==="
echo ""

# ── 1. Build ─────────────────────────────────────────────────────────────────

echo "[1/8] Building CloudDump image..."
docker build -q -t clouddump:integration-test "$REPO_DIR" >/dev/null
echo "  Image built."

# ── 2. Generate SSH keys ────────────────────────────────────────────────────

echo "[2/8] Generating SSH test key pair..."
mkdir -p "$SCRIPT_DIR/test-keys"
ssh-keygen -t ed25519 -f "$SCRIPT_DIR/test-keys/id_test" -N "" -q -C "integration-test"
echo "  Keys generated."

# ── 3. Generate runtime config (adds mount entries + mount-based jobs) ──────

echo "[3/8] Generating runtime config..."
python3 -c "
import json, sys

with open(sys.argv[1]) as f:
    config = json.load(f)

privkey = open(sys.argv[2]).read()

config['settings']['mount'] = [
    {
        'path': 'testuser@sshserver:/upload',
        'mountpoint': '/mnt/ssh',
        'privkey': privkey,
    },
    {
        'path': '//samba/testshare',
        'mountpoint': '/mnt/smb',
        'username': 'testuser',
        'password': 'testpass',
    },
]

config['jobs'].append({
    'type': 's3bucket',
    'id': 'test-s3-via-ssh',
    'crontab': '* * * * *',
    'retries': 1,
    'timeout': 300,
    'buckets': [{
        'source': 's3://test-bucket-ssh',
        'destination': '/mnt/ssh/s3-backup',
        'endpoint_url': 'http://minio:9000',
        'aws_access_key_id': 'minioadmin',
        'aws_secret_access_key': 'minioadmin',
        'aws_region': 'us-east-1',
        'delete_destination': 'true',
    }],
})

config['jobs'].append({
    'type': 's3bucket',
    'id': 'test-s3-via-smb',
    'crontab': '* * * * *',
    'retries': 1,
    'timeout': 300,
    'buckets': [{
        'source': 's3://test-bucket-smb',
        'destination': '/mnt/smb/s3-backup',
        'endpoint_url': 'http://minio:9000',
        'aws_access_key_id': 'minioadmin',
        'aws_secret_access_key': 'minioadmin',
        'aws_region': 'us-east-1',
        'delete_destination': 'true',
    }],
})

with open(sys.argv[3], 'w') as f:
    json.dump(config, f, indent=2)
" "$SCRIPT_DIR/config.json" "$SCRIPT_DIR/test-keys/id_test" "$SCRIPT_DIR/config.runtime.json"
echo "  Config written to config.runtime.json"

# ── 4. Start fakes ──────────────────────────────────────────────────────────

echo "[4/8] Starting fake services (MinIO, PostgreSQL, Azurite, Mailpit, SSH, Samba)..."
$COMPOSE up -d --wait
echo "  All services healthy."

# ── 5. Seed ──────────────────────────────────────────────────────────────────

echo "[5/8] Seeding test data..."
bash "$SCRIPT_DIR/seed.sh"

# ── 6. Run CloudDump ────────────────────────────────────────────────────────

echo "[6/8] Starting CloudDump container..."
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR/s3" "$BACKUP_DIR/pgsql" "$BACKUP_DIR/azure"

docker run -d --name "$CONTAINER" \
    --network clouddump-integration \
    --cap-add SYS_ADMIN \
    --device /dev/fuse \
    --security-opt apparmor:unconfined \
    -v "$SCRIPT_DIR/config.runtime.json:/config/config.json:ro" \
    -v "$BACKUP_DIR:/backup" \
    clouddump:integration-test >/dev/null

echo "  Container started (FUSE enabled for sshfs/smbnetfs)."

# ── 7. Wait & verify ────────────────────────────────────────────────────────

echo "[7/8] Waiting for jobs to complete (polling every 5s, up to 150s)..."
DONE=false
for i in $(seq 1 30); do
    # Check that CloudDump is still running (mounts would exit immediately on failure)
    if ! docker inspect "$CONTAINER" --format='{{.State.Running}}' 2>/dev/null | grep -q true; then
        echo "  WARNING: container exited early — mount failure?"
        break
    fi

    # S3 local + pgsql + mount-based syncs all finished?
    if [ -f "$BACKUP_DIR/s3/file1.txt" ] \
        && compgen -G "$BACKUP_DIR/pgsql/"*.bz2 >/dev/null 2>&1 \
        && docker exec "$CONTAINER" test -f /mnt/ssh/s3-backup/via-ssh.txt 2>/dev/null \
        && docker exec "$CONTAINER" test -f /mnt/smb/s3-backup/via-smb.txt 2>/dev/null; then
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
check "CloudDump is still running (mounts OK)" \
    docker inspect "$CONTAINER" --format='{{.State.Running}}' 2>/dev/null

echo ""
echo "  S3 sync (local volume):"
check "file1.txt exists"                test -f "$BACKUP_DIR/s3/file1.txt"
check "file1.txt has expected content"  grep -q "file1" "$BACKUP_DIR/s3/file1.txt"
check "subdir/file2.txt exists"         test -f "$BACKUP_DIR/s3/subdir/file2.txt"
check "subdir/nested/file3.txt exists"  test -f "$BACKUP_DIR/s3/subdir/nested/file3.txt"

echo ""
echo "  PostgreSQL dump:"
check "testuser dump exists and non-empty"  test -s "$BACKUP_DIR/pgsql/testuser.tar.bz2"
check "testdb1 dump exists and non-empty"   test -s "$BACKUP_DIR/pgsql/testdb1.tar.bz2"
check "testdb2 dump exists and non-empty"   test -s "$BACKUP_DIR/pgsql/testdb2.tar.bz2"

echo ""
echo "  S3 sync via sshfs mount:"
check "via-ssh.txt reached CloudDump mount" \
    docker exec "$CONTAINER" test -f /mnt/ssh/s3-backup/via-ssh.txt
check "via-ssh.txt arrived on SSH server" \
    $COMPOSE exec -T sshserver test -f /home/testuser/upload/s3-backup/via-ssh.txt

echo ""
echo "  S3 sync via smbnetfs mount:"
check "via-smb.txt reached CloudDump mount" \
    docker exec "$CONTAINER" test -f /mnt/smb/s3-backup/via-smb.txt
check "via-smb.txt arrived on Samba server" \
    $COMPOSE exec -T samba test -f /share/s3-backup/via-smb.txt

echo ""
echo "  Email (SMTP):"
echo "  SKIP  CloudDump uses SMTP_SSL; Mailpit only supports plain SMTP."
echo "        Mailpit web UI: http://localhost:8025 (with --keep)"

# ── 8. Tool & Azure smoke tests ─────────────────────────────────────────────

echo ""
echo "[8/8] Running tool & Azure smoke tests..."
echo ""

echo "  Bundled tools (in Docker image):"
check "github-backup installed"  docker exec "$CONTAINER" github-backup --help
check "git installed"            docker exec "$CONTAINER" git --version
check "aws CLI installed"        docker exec "$CONTAINER" aws --version
check "azcopy installed"         docker exec "$CONTAINER" azcopy --version
check "pg_dump installed"        docker exec "$CONTAINER" pg_dump --version
check "psql installed"           docker exec "$CONTAINER" psql --version

echo ""
echo "  Azure Blob Storage (azcopy → Azurite, direct):"

# Generate a SAS URL for azcopy using Python (well-known Azurite credentials).
# CloudDump's Azure runner requires https:// so we can't run it E2E against
# Azurite on HTTP, but we CAN verify azcopy works against real blob storage.
AZURITE_SAS=$(docker exec "$CONTAINER" python3 -c "
import base64, hashlib, hmac, datetime, urllib.parse

key = base64.b64decode(
    'Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq'
    '/K1SZFPTOtr/KBHBeksoGMGw=='
)
expiry = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
start  = (datetime.datetime.utcnow() - datetime.timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
perms  = 'rl'
# Account SAS string-to-sign (ss=b covers Blob service)
sts = '\n'.join([
    'devstoreaccount1',  # account name
    perms,               # sp
    'b',                 # ss (blob)
    'sco',               # srt (service, container, object)
    start,               # st
    expiry,              # se
    '',                  # sip
    'http,https',        # spr
    '2020-10-02',        # sv
    '',                  # sr (encryption scope - empty)
])
sig = base64.b64encode(hmac.new(key, sts.encode(), hashlib.sha256).digest()).decode()
print(f'sv=2020-10-02&ss=b&srt=sco&sp={perms}&st={urllib.parse.quote(start)}&se={urllib.parse.quote(expiry)}&spr=http,https&sig={urllib.parse.quote(sig)}')
")

docker exec "$CONTAINER" azcopy sync \
    "http://azurite:10000/devstoreaccount1/test-container?${AZURITE_SAS}" \
    "/backup/azure" --recursive >/dev/null 2>&1 || true

check "blob1.txt synced via azcopy"         test -f "$BACKUP_DIR/azure/blob1.txt"
check "subdir/blob2.txt synced via azcopy"  test -f "$BACKUP_DIR/azure/subdir/blob2.txt"

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
