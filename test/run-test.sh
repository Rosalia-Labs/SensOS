#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="${BASE_DIR}/test"
SERVER_SRC="${BASE_DIR}/server"
CLIENT_SRC="${BASE_DIR}/client/sensos/stage-base"
SERVER_DST="${TEST_DIR}/server"
CLIENT_DST="${TEST_DIR}/client"

SERVER_TAR="${SERVER_DST}/server.tar.gz"
CLIENT_TAR="${CLIENT_DST}/client.tar.gz"

cleanup() {
    echo "🧹 Cleaning up test artifacts..."
    rm -f "$SERVER_TAR" "$CLIENT_TAR"
}
trap cleanup EXIT INT TERM

echo "📦 Creating tarballs..."

# Create tarball of the server code
tar -czf "$SERVER_TAR" -C "$SERVER_SRC" .

# Create tarball of the client code
tar -czf "$CLIENT_TAR" -C "$CLIENT_SRC" .

echo "✅ Tarballs ready:"
echo " - $SERVER_TAR"
echo " - $CLIENT_TAR"

# 🚧 TODO: Build the Docker images
echo "🔧 Building test containers..."
docker compose -f "$TEST_DIR/docker-compose.yml" build

# 🚧 TODO: Start containers using docker-compose.yml
# docker compose -f "$TEST_DIR/docker-compose.yml" up

# 🚧 TODO: Add logic for verifying startup or running config scripts inside client

# 🚧 TODO: Stop containers and clean up Docker volumes/networks if needed
