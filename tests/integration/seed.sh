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

# Use Python (available in the CloudDump image) to create a container and
# upload blobs via the Azure Blob Storage REST API.  The well-known Azurite
# account key is used for HMAC-SHA256 authentication.
docker run --rm --network clouddump-integration \
  clouddump:integration-test python3 -c "
import base64, hashlib, hmac, urllib.request, datetime

ACCOUNT = 'devstoreaccount1'
KEY = base64.b64decode(
    'Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq'
    '/K1SZFPTOtr/KBHBeksoGMGw=='
)
HOST = 'azurite:10000'
CONTAINER = 'test-container'

def sign_and_send(method, resource, body=b'', extra_headers=None, query_params=None):
    \"\"\"Send an authenticated request to Azurite using SharedKey.

    *resource* is the path after the account, e.g. '/test-container/blob.txt'.
    *query_params* is a dict of query-string parameters (used in both the URL
    and the canonicalised resource for signing).
    \"\"\"
    now = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    headers = {
        'x-ms-date': now,
        'x-ms-version': '2020-10-02',
    }
    if extra_headers:
        headers.update(extra_headers)
    content_length = str(len(body)) if body else ''
    content_type = headers.get('Content-Type', '')

    # Canonicalised headers
    canon_h = '\n'.join(f'{k}:{v}' for k, v in sorted(
        (k.lower(), v) for k, v in headers.items() if k.lower().startswith('x-ms-')
    ))

    # Canonicalised resource: /{account}{resource}\n{key}:{value} per query param
    canon_r = f'/{ACCOUNT}{resource}'
    if query_params:
        for k in sorted(query_params):
            canon_r += f'\n{k}:{query_params[k]}'

    sts = '\n'.join([
        method, '', '', content_length, '', content_type,
        '', '', '', '', '', '',
        canon_h, canon_r,
    ])
    sig = base64.b64encode(
        hmac.new(KEY, sts.encode(), hashlib.sha256).digest()
    ).decode()
    headers['Authorization'] = f'SharedKey {ACCOUNT}:{sig}'
    if body:
        headers['Content-Length'] = content_length

    # Build URL: http://host/{account}{resource}?query
    url = f'http://{HOST}/{ACCOUNT}{resource}'
    if query_params:
        url += '?' + '&'.join(f'{k}={v}' for k, v in sorted(query_params.items()))

    req = urllib.request.Request(url, data=body or None, headers=headers, method=method)
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        if e.code == 409:  # container already exists
            pass
        else:
            raise

# Create container
sign_and_send('PUT', f'/{CONTAINER}', query_params={'restype': 'container'})

# Upload blobs
for name, content in [
    ('blob1.txt', 'Hello from Azurite - blob1'),
    ('subdir/blob2.txt', 'Hello from Azurite - blob2'),
]:
    sign_and_send('PUT', f'/{CONTAINER}/{name}', content.encode(), {
        'Content-Type': 'application/octet-stream',
        'x-ms-blob-type': 'BlockBlob',
    })

print('  Azurite seeded: test-container (2 blobs)')
"

echo "  Seeding complete."
