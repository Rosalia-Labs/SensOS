#!/bin/bash
set -euo pipefail

CONFIG_FILE="/sensos/etc/network.conf"
API_PATH="/"
API_USER="sensos"
API_PASS_FILE="/sensos/keys/api_password"

# Load config
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "[ERROR] $CONFIG_FILE not found!"
    exit 1
fi

declare -A CFG
while IFS='=' read -r key value; do
    [[ -z "$key" ]] && continue
    CFG["$key"]="$value"
done <"$CONFIG_FILE"

SERVER_WG_IP="${CFG[SERVER_WG_IP]}"
SERVER_PORT="${CFG[SERVER_PORT]}"

if [[ -z "${SERVER_WG_IP:-}" || -z "${SERVER_PORT:-}" ]]; then
    echo "[ERROR] SERVER_WG_IP or SERVER_PORT missing in $CONFIG_FILE"
    exit 1
fi

if [[ ! -f "$API_PASS_FILE" ]]; then
    echo "[ERROR] API password file $API_PASS_FILE not found!"
    exit 1
fi

API_PASS="$(<"$API_PASS_FILE")"

API_URL="http://${SERVER_WG_IP}:${SERVER_PORT}${API_PATH}"

echo "[INFO] Probing API at $API_URL as $API_USER"
if curl -fsSL --max-time 5 -u "$API_USER:$API_PASS" "$API_URL" >/dev/null; then
    echo "[SUCCESS] API is reachable at $API_URL"
    exit 0
else
    echo "[FAILURE] API is NOT reachable at $API_URL"
    exit 1
fi
