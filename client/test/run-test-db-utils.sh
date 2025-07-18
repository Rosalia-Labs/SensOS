#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SOURCE="$SCRIPT_DIR/../sensos/stage-base/00-sensos/files/docker/db_manager/db_utils.py"
NETWORK_NAME="testnet"

# Clean up containers/network on exit
cleanup() {
  docker rm -f test-pg >/dev/null 2>&1 || true
  docker network rm $NETWORK_NAME >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Create network if it doesn't exist
docker network inspect $NETWORK_NAME >/dev/null 2>&1 || docker network create $NETWORK_NAME

# Start Postgres container (no host port mapping)
docker run --rm -d --name test-pg \
  --network $NETWORK_NAME \
  -e POSTGRES_USER=testuser \
  -e POSTGRES_PASSWORD=testpass \
  -e POSTGRES_DB=testdb \
  postgres:15

# Wait for Postgres to be ready (simple wait loop)
for i in {1..10}; do
  if docker run --rm --network $NETWORK_NAME \
    postgres:15 pg_isready -h test-pg -p 5432 -U testuser; then
    break
  fi
  sleep 1
done

# Run your Python test inside another container on the same network
docker run --rm \
  --network $NETWORK_NAME \
  -v "$PY_SOURCE":/test/db_utils.py:ro \
  -v "$PWD/test_db_utils.py":/test/test_db_utils.py:ro \
  python:3.11-slim bash -c $'
set -e
pip install psycopg[binary]
python3 /test/test_db_utils.py
'
