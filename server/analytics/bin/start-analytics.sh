#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -euo pipefail

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORK_DIR="$REPO_ROOT/server/analytics/docker"

cd "$WORK_DIR"
echo "Working directory: $(pwd)"

# Defaults
REBUILD=false
NO_CACHE=false
DETACH=true
RESTART=false

ENV_FILE="$WORK_DIR/.env"
COMPOSE_FILE="$WORK_DIR/docker-compose.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ .env file not found at $ENV_FILE. Run analytics/bin/config-docker first." >&2
  exit 1
fi

# Parse CLI args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild|--rebuild-containers) REBUILD=true ;;
    --no-cache) NO_CACHE=true ;;
    --no-detach) DETACH=false ;;
    --restart) RESTART=true ;;
    --help)
      echo "Usage: $0 [--rebuild] [--no-cache] [--no-detach] [--restart]"
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

# If containers already running, handle --restart or exit
if docker ps --filter "name=sensos-analytics-" --format '{{.Names}}' | grep -q .; then
  if [[ "$RESTART" == true ]]; then
    echo "ℹ️  Restart requested. Stopping analytics stack..."
    "$SCRIPT_DIR/stop-analytics.sh"
  else
    echo "❌ Analytics containers already running. Use --restart or run stop-analytics.sh first." >&2
    exit 1
  fi
fi

# Optional build
if [[ "$REBUILD" == true ]]; then
  BUILD_CMD=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build)
  if [[ "$NO_CACHE" == true ]]; then
    BUILD_CMD+=(--no-cache)
  fi
  echo "🔨 Building analytics containers: ${BUILD_CMD[*]}"
  "${BUILD_CMD[@]}"
fi

# Up
UP_CMD=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up)
if [[ "$DETACH" == true ]]; then
  UP_CMD+=(-d)
fi

echo "🚀 Starting analytics stack: ${UP_CMD[*]}"
exec "${UP_CMD[@]}"
