#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

CONFIG_SERVER="${CONFIG_SERVER:-sensos-test-server}"
SERVICE_CLIENT="${SERVICE_CLIENT:-client}"
SERVICE_SERVER="${SERVICE_SERVER:-server}"
SERVER_PORT="${SERVER_PORT:-8765}"
RUN_AS_USER="${RUN_AS_USER:-sensos-admin}"
COMPOSE="${COMPOSE:-docker compose}"
HEALTH_PATH="${HEALTH_PATH:-/health}"   # set to "" if your server has no health endpoint
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_DELAY_SECS="${HEALTH_DELAY_SECS:-1}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

cleanup() {
  echo ">> Tearing down the SensOS DinD testbed…"
  $COMPOSE down -v || true
}
trap 'rc=$?; cleanup; exit $rc' EXIT

echo ">> Removing any prior testbed state…"
$COMPOSE down -v || true

echo ">> Building SensOS DinD testbed…"
$COMPOSE build --pull

echo ">> Starting stack and waiting for health checks…"
$COMPOSE up -d --wait --quiet-pull

echo ">> Checking server manage scripts exist…"
$COMPOSE exec -T "$SERVICE_SERVER" sh -lc '
  set -e
  test -x /srv/sensos-server/bin/configure-server.sh
  test -x /srv/sensos-server/bin/start-server.sh
'

echo ">> Configuring and starting the server…"
$COMPOSE exec -T "$SERVICE_SERVER" /srv/sensos-server/bin/configure-server.sh
$COMPOSE exec -T "$SERVICE_SERVER" /srv/sensos-server/bin/start-server.sh

if [[ -n "$HEALTH_PATH" ]]; then
  echo ">> Waiting for server health at http://$CONFIG_SERVER:$SERVER_PORT$HEALTH_PATH …"
  i=0
  until $COMPOSE exec -T "$SERVICE_SERVER" sh -lc \
      "command -v curl >/dev/null || (apk add -q --no-progress curl 2>/dev/null || (apt-get update -qq && apt-get install -y -qq curl)); \
       curl -fsS http://127.0.0.1:$SERVER_PORT$HEALTH_PATH >/dev/null"; do
    ((i++))
    if (( i >= HEALTH_RETRIES )); then
      echo "❌ Server did not become healthy after $((HEALTH_RETRIES*HEALTH_DELAY_SECS))s"
      exit 1
    fi
    sleep "$HEALTH_DELAY_SECS"
  done
  echo "✅ Server healthy."
fi

echo ">> Testing config-network…"
$COMPOSE exec -T --user "$RUN_AS_USER" "$SERVICE_CLIENT" \
  config-network --config-server "$CONFIG_SERVER" --port "$SERVER_PORT"

echo ">> Test completed successfully."
