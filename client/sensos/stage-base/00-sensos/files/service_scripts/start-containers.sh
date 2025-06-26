#!/bin/bash
set -e

script_name=$(basename "$0")

source /sensos/lib/load-defaults.sh
source /sensos/lib/parse-switches.sh
source /sensos/lib/docker-utils.sh

load_defaults /sensos/etc/defaults.conf "$script_name"

# Register CLI options
register_option --detach DETACH_MODE "Run containers in the background" "false"

parse_switches "$script_name" "$@"

cd /sensos/docker

# Verify .env exists
if [[ ! -f .env ]]; then
    echo "ERROR: Missing /sensos/docker/.env — run config-containers.sh first." >&2
    exit 1
fi

# Load images from tarballs (always safe)
echo "[INFO] Loading any available images from local tarballs..."
load_images_from_disk

# Ensure directories
sudo mkdir -p /sensos/data/microenv
sudo chown -R sensos-admin:sensos-data /sensos/data/microenv
sudo chmod -R 2775 /sensos/data/microenv

# Prepare Docker Compose command
COMPOSE_CMD=(docker compose)

if [[ "$DETACH_MODE" == "true" ]]; then
    COMPOSE_CMD+=(up -d)
else
    COMPOSE_CMD+=(up)
fi

"${COMPOSE_CMD[@]}"
