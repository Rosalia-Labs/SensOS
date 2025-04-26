#!/bin/bash
set -euo pipefail

# Config
MODULE_FILE="../../controller/wireguard.py"
TEST_FILE="test.py"
WORKDIR="$(mktemp -d)"

# Create temporary workspace
cp "$MODULE_FILE" "$TEST_FILE" "$WORKDIR/"

# Run docker
docker run --rm -it \
    -v "$WORKDIR":/app \
    -w /app \
    debian:bookworm-slim \
    bash -c "
      apt-get update && \
      apt-get install -y python3 python3-pip python3-venv wireguard-tools && \
      python3 -m venv venv && \
      source venv/bin/activate && \
      pip install --upgrade pip && \
      pip install pytest && \
      pytest test.py
    "
