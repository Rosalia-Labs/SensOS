#!/bin/bash
set -e

source /sensos/lib/load-defaults.sh
source /sensos/lib/parse-switches.sh
source /sensos/lib/docker-utils.sh

load_defaults /sensos/etc/defaults.conf "$(basename "$0")"

# Register options
register_option "--offline" "OFFLINE_MODE" "Force offline mode (disables pulls)" "false"
register_option "--build" "NEEDS_BUILD" "Build images before starting" "false"
register_option "--detach" "DETACH_MODE" "Run containers in the background" "false"
register_option "--compose-file" "COMPOSE_FILE" "Path to docker-compose file" "docker-compose.yml"

# Parse CLI args
parse_switches "$0" "$@"

# Apply config-based connectivity rule if not overridden by --offline
: "${connectivity:=unrestricted}"
case "$connectivity" in
none | restricted)
    OFFLINE=true
    ;;
unrestricted)
    OFFLINE=false
    ;;
*)
    echo "Warning: Unknown connectivity level: $connectivity"
    OFFLINE=true
    ;;
esac

# CLI flag overrides config
if [[ "$OFFLINE_MODE" == true ]]; then
    OFFLINE=true
fi

echo "[INFO] Preloading images from disk..."
load_missing_images_from_disk

# Prepare Docker Compose command
COMPOSE_CMD=(docker compose -f "$COMPOSE_FILE")

if [[ "$OFFLINE" == true ]]; then
    echo "[INFO] Offline mode: disabling remote pulls"
    export DOCKER_BUILDKIT=1
    export COMPOSE_DOCKER_CLI_BUILD=1
    COMPOSE_CMD+=("--pull" "never")
fi

if [[ "$NEEDS_BUILD" == true ]]; then
    COMPOSE_CMD+=("--build")
fi

echo "[INFO] Starting containers..."
if [[ "$DETACH_MODE" == true ]]; then
    "${COMPOSE_CMD[@]}" up -d
else
    "${COMPOSE_CMD[@]}" up
fi
