#!/bin/bash
set -euo pipefail

# Config
MODULE_FILES=("../../docker/controller/core.py" "../../docker/controller/wireguard.py" "../../docker/controller/api.py" "../../docker/controller/models.py")
TEST_FILE="test.py"
WORKDIR="$(mktemp -d)"

# Create temporary workspace
for file in "${MODULE_FILES[@]}"; do
  cp "$file" "$WORKDIR/"
done
cp "$TEST_FILE" "$WORKDIR/"

# Run docker
docker run --rm -it \
  -v "$WORKDIR":/app \
  -w /app \
  -e POSTGRES_PASSWORD=dummy \
  -e API_PASSWORD=dummy \
  debian:bookworm-slim \
  bash -c "
      apt-get update && \
      apt-get install -y --no-install-recommends python3 python3-pip python3-venv wireguard-tools && \
      python3 -m venv venv && \
      source venv/bin/activate && \
      pip install --upgrade pip && \
      pip install pytest psycopg[binary] docker fastapi pytest-asyncio httpx python-multipart && \
      pytest test.py
    "
