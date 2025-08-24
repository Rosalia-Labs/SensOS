#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -e

# Determine the project root (assumes the script is in ./bin)
WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$WORK_DIR"

echo "Working directory: $(pwd)"

# Create backup directory and timestamp
BACKUP_DIR="./backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/db_backup_${TIMESTAMP}.gz"

# Check that the sensos-database container is running.
if ! docker ps --filter "name=^sensos-database$" --format '{{.Names}}' | grep -q "^sensos-database$"; then
    echo "❌ sensos-database container is not running. Exiting."
    exit 1
fi

echo "💾 Backing up database from sensos-database to $BACKUP_FILE..."
# Note: We omit the -t flag to avoid allocating a TTY, which can corrupt binary data.
docker exec sensos-database bash -c "pg_dumpall -U postgres | gzip" >"$BACKUP_FILE"

if [ $? -eq 0 ] && [ -s "$BACKUP_FILE" ]; then
    echo "✅ Database backup completed successfully: $BACKUP_FILE"
else
    echo "❌ Database backup failed or produced an empty backup."
    exit 1
fi
