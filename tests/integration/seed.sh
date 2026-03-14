#!/usr/bin/env bash
# Populate the fake services with test data.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="docker compose -f $SCRIPT_DIR/docker-compose.yml"

# ── MinIO (S3) ───────────────────────────────────────────────────────────────

echo "  Seeding MinIO..."
docker run --rm --network clouddump-integration \
  --entrypoint sh minio/mc -c '
    mc alias set local http://minio:9000 minioadmin minioadmin &&
    mc mb --ignore-existing local/test-bucket &&
    echo "Hello from integration test - file1" | mc pipe local/test-bucket/file1.txt &&
    echo "Hello from integration test - file2" | mc pipe local/test-bucket/subdir/file2.txt &&
    echo "Hello from integration test - file3" | mc pipe local/test-bucket/subdir/nested/file3.txt &&
    mc mb --ignore-existing local/test-bucket-ssh &&
    echo "Synced via SSH mount" | mc pipe local/test-bucket-ssh/via-ssh.txt &&
    mc mb --ignore-existing local/test-bucket-smb &&
    echo "Synced via SMB mount" | mc pipe local/test-bucket-smb/via-smb.txt
  '
echo "  MinIO seeded: test-bucket (3 files), test-bucket-ssh (1), test-bucket-smb (1)"

# ── PostgreSQL ───────────────────────────────────────────────────────────────

echo "  Seeding PostgreSQL..."

$COMPOSE exec -T postgres psql -U testuser -c \
  "CREATE DATABASE testdb1;" 2>/dev/null || true

$COMPOSE exec -T postgres psql -U testuser -d testdb1 -c "
  CREATE TABLE IF NOT EXISTS users (
    id    SERIAL PRIMARY KEY,
    name  TEXT,
    email TEXT
  );
  INSERT INTO users (name, email) VALUES
    ('Alice',   'alice@example.com'),
    ('Bob',     'bob@example.com'),
    ('Charlie', 'charlie@example.com');
"

$COMPOSE exec -T postgres psql -U testuser -c \
  "CREATE DATABASE testdb2;" 2>/dev/null || true

$COMPOSE exec -T postgres psql -U testuser -d testdb2 -c "
  CREATE TABLE IF NOT EXISTS products (
    id    SERIAL PRIMARY KEY,
    name  TEXT,
    price NUMERIC
  );
  INSERT INTO products (name, price) VALUES
    ('Widget', 9.99), ('Gadget', 19.99), ('Thingamajig', 29.99);
  CREATE TABLE IF NOT EXISTS orders (
    id         SERIAL PRIMARY KEY,
    product_id INT,
    quantity   INT
  );
  INSERT INTO orders (product_id, quantity) VALUES (1, 5), (2, 3), (3, 1);
"

echo "  PostgreSQL seeded: testdb1 (users), testdb2 (products + orders)"

# ── Azurite (Azure Blob Storage) ────────────────────────────────────────────

echo "  Seeding Azurite..."

# Seed Azurite using azcopy from the CloudDump image with the well-known
# Azurite connection string.  Much simpler than hand-rolling SharedKey HMAC.
AZURITE_CONN="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://azurite:10000/devstoreaccount1;"

docker run --rm --network clouddump-integration \
  -e AZURE_STORAGE_CONNECTION_STRING="$AZURITE_CONN" \
  clouddump:integration-test sh -c '
    mkdir -p /tmp/azurite-seed/subdir
    echo "Hello from Azurite - blob1" > /tmp/azurite-seed/blob1.txt
    echo "Hello from Azurite - blob2" > /tmp/azurite-seed/subdir/blob2.txt
    azcopy copy "/tmp/azurite-seed/*" "http://azurite:10000/devstoreaccount1/test-container" \
      --recursive --put-md5 --from-to LocalBlob
  '
echo "  Azurite seeded: test-container (2 blobs)"

echo "  Seeding complete."
