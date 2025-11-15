#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUPS_DIR="$PROJECT_ROOT/backups"
CONTAINER_NAME="sensos-analytics-database"

mkdir -p "$BACKUPS_DIR"
STAMP=$(date +"%Y%m%d_%H%M%S")
OUT_FILE="$BACKUPS_DIR/analytics_db_${STAMP}.sql.gz"

if ! docker ps --filter "name=^${CONTAINER_NAME}$" --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "❌ ${CONTAINER_NAME} is not running. Exiting." >&2
  exit 1
fi

echo "💾 Backing up analytics database to $OUT_FILE ..."
# Use pg_dumpall to capture roles and all DBs; if per-DB is desired, switch to pg_dump -d "$POSTGRES_DB"
docker exec "$CONTAINER_NAME" bash -c "pg_dumpall -U ${POSTGRES_USER:-sensos} | gzip" > "$OUT_FILE"

if [[ -s "$OUT_FILE" ]]; then
  echo "✅ Backup complete: $OUT_FILE"
else
  echo "❌ Backup failed or empty output." >&2
  exit 1
fi
