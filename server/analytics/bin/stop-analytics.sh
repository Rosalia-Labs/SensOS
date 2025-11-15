#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORK_DIR="$REPO_ROOT/server/analytics/docker"

cd "$WORK_DIR"
echo "Working directory: $(pwd)"

REMOVE_VOLUMES=false
BACKUP=false
NO_BACKUP=false

ENV_FILE="$WORK_DIR/.env"
COMPOSE_FILE="$WORK_DIR/docker-compose.yml"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remove-volumes) REMOVE_VOLUMES=true ;;
    --backup) BACKUP=true ;;
    --no-backup) NO_BACKUP=true ;;
    --help)
      echo "Usage: $0 [--remove-volumes] [--backup] [--no-backup]"
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

# Determine if backup should run
PERFORM_BACKUP=false
if [[ "$NO_BACKUP" == false ]] && { [[ "$BACKUP" == true ]] || [[ "$REMOVE_VOLUMES" == true ]]; }; then
  PERFORM_BACKUP=true
fi

if [[ "$PERFORM_BACKUP" == true ]]; then
  echo "💾 Running analytics DB backup before shutdown..."
  "$SCRIPT_DIR/backup-analytics-database.sh" || {
    echo "❌ Backup failed; will not remove volumes." >&2
    REMOVE_VOLUMES=false
  }
fi

# Stop stack
echo "🛑 Stopping analytics docker compose..."
if [[ "$REMOVE_VOLUMES" == true ]]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down -v
else
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down
fi

echo "✅ Analytics stack stopped."
