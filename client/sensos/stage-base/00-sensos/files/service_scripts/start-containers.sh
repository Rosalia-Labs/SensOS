#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

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

ensure_dashboard_firewall_rule() {
    local dashboard_port nft_include nft_conf dashboard_rule
    dashboard_port="$(grep -E '^DASHBOARD_PORT=' .env | tail -n1 | cut -d= -f2- | tr -d '[:space:]')"
    [[ -z "$dashboard_port" ]] && dashboard_port="8090"
    if [[ ! "$dashboard_port" =~ ^[0-9]+$ ]] || ((dashboard_port < 1 || dashboard_port > 65535)); then
        echo "[WARN] Invalid DASHBOARD_PORT='$dashboard_port' in .env; using 8090"
        dashboard_port="8090"
    fi

    nft_include="/sensos/etc/sensos-ports.nft"
    nft_conf="/sensos/etc/nftables.conf"
    dashboard_rule="add rule inet filter input tcp dport ${dashboard_port} accept"

    sudo touch "$nft_include"
    if ! sudo grep -Fqx "$dashboard_rule" "$nft_include"; then
        echo "[INFO] Allowing dashboard port ${dashboard_port}/tcp through nftables..."
        echo "$dashboard_rule" | sudo tee -a "$nft_include" >/dev/null
        if [[ -f "$nft_conf" ]]; then
            if sudo nft -f "$nft_conf"; then
                echo "[INFO] Reloaded nftables with dashboard rule."
            else
                echo "[WARN] Failed to reload nftables; dashboard may stay blocked until firewall reload."
            fi
        fi
    fi
}

ensure_dashboard_firewall_rule

# Load images from tarballs (always safe)
echo "[INFO] Loading any available images from local tarballs..."
load_images_from_disk

# Ensure directories
sudo mkdir -p /sensos/data/microenv
sudo chown -R sensos-admin:sensos-data /sensos/data/microenv
sudo chmod -R 2775 /sensos/data/microenv

if [[ -f /sensos/etc/location.conf ]]; then
    source /sensos/etc/location.conf
    echo "Latitude = $LATITUDE Longitude = $LONGITUDE"
    export LATITUDE LONGITUDE
else
    echo "WARNING: Missing /sensos/etc/location.conf" >&2
fi

# Prepare Docker Compose command
COMPOSE_CMD=(docker compose)

if [[ "$DETACH_MODE" == "true" ]]; then
    COMPOSE_CMD+=(up -d)
else
    COMPOSE_CMD+=(up)
fi

"${COMPOSE_CMD[@]}"
