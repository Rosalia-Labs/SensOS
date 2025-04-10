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

# Parse CLI args
parse_switches "$0" "$@"

cd /sensos/docker

# Verify that .env exists
if [[ ! -f .env ]]; then
    echo "ERROR: Missing /sensos/docker/.env — run config-docker first." >&2
    exit 1
fi

# Load connectivity profile from .env
connectivity_profile="unrestricted"
while IFS='=' read -r key value; do
    if [[ "$key" == "CONNECTIVITY_PROFILE" ]]; then
        connectivity_profile="$value"
    fi
done <.env

# Determine effective offline mode
OFFLINE=false
case "$connectivity_profile" in
offline | restricted)
    OFFLINE=true
    ;;
unrestricted)
    OFFLINE=false
    ;;
*)
    echo "Warning: Unknown connectivity profile in .env: $connectivity_profile"
    OFFLINE=true
    ;;
esac

# CLI flag overrides config-based offline mode
if [[ "$OFFLINE_MODE" == true ]]; then
    OFFLINE=true
fi

echo "[INFO] Preloading images from disk..."
load_missing_images_from_disk

# Prepare Docker Compose command
COMPOSE_CMD=(docker compose)

if [[ "$DETACH_MODE" == true ]]; then
    COMPOSE_CMD+=(up -d)
else
    COMPOSE_CMD+=(up)
fi

if [[ "$OFFLINE" == true ]]; then
    echo "[INFO] Offline mode: disabling remote pulls"
    export DOCKER_BUILDKIT=1
    export COMPOSE_DOCKER_CLI_BUILD=1
    COMPOSE_CMD+=(--pull never)
fi

if [[ "$NEEDS_BUILD" == true ]]; then
    COMPOSE_CMD+=(--build)
fi

"${COMPOSE_CMD[@]}"
