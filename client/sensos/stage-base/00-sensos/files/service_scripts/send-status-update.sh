#!/bin/bash
set -euo pipefail

source /sensos/lib/parse-switches.sh

CONFIG_FILE="/sensos/etc/network.conf"
API_PATH="/client-status"
API_USER="sensos"
API_PASS_FILE="/sensos/keys/api_password"
VERSION_FILE="/sensos/etc/sensos-version"

if [[ ! -f "$VERSION_FILE" ]]; then
    echo "[ERROR] $VERSION_FILE not found!"
    exit 1
fi

VERSION=$(grep "^VERSION=" "$VERSION_FILE" | head -n1 | cut -d'=' -f2 | tr -d '"')

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
WIREGUARD_IP="${CFG[CLIENT_WG_IP]:-}" # Get client IP if present

if [[ -z "${SERVER_WG_IP:-}" || -z "${SERVER_PORT:-}" || -z "${WIREGUARD_IP:-}" ]]; then
    echo "[ERROR] SERVER_WG_IP, SERVER_PORT, or CLIENT_WG_IP missing in $CONFIG_FILE"
    exit 1
fi

if [[ ! -f "$API_PASS_FILE" ]]; then
    echo "[ERROR] API password file $API_PASS_FILE not found!"
    exit 1
fi

API_PASS="$(<"$API_PASS_FILE")"
API_URL="http://${SERVER_WG_IP}:${SERVER_PORT}${API_PATH}"

# === Gather metrics ===

hostname="$(hostname)"

# Uptime in seconds
uptime_seconds="$(awk '{print int($1)}' /proc/uptime)"

# Disk available GB (root filesystem)
disk_available_gb="$(df --output=avail -BG / | tail -1 | tr -dc '0-9')"

# Memory info (MB)
mem_total_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
mem_available_kb="$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
mem_total_mb="$((mem_total_kb / 1024))"
mem_used_mb="$(((mem_total_kb - mem_available_kb) / 1024))"

# Load averages
read load_1m load_5m load_15m _ </proc/loadavg

status_message="OK"

# Build JSON payload
json_payload=$(
    jq -n \
        --arg hn "$hostname" \
        --argjson up "$uptime_seconds" \
        --argjson disk "$disk_available_gb" \
        --argjson used "$mem_used_mb" \
        --argjson total "$mem_total_mb" \
        --argjson l1 "$load_1m" \
        --argjson l5 "$load_5m" \
        --argjson l15 "$load_15m" \
        --arg ver "$VERSION" \
        --arg sm "$status_message" \
        --arg ip "$WIREGUARD_IP" \
        '{
        hostname: $hn,
        uptime_seconds: $up,
        disk_available_gb: $disk,
        memory_used_mb: $used,
        memory_total_mb: $total,
        load_1m: $l1,
        load_5m: $l5,
        load_15m: $l15,
        version: $ver,
        status_message: $sm,
        wireguard_ip: $ip
    }'
)

# === POST status ===

echo "[INFO] Sending status to $API_URL"

curl -fsSL -u "$API_USER:$API_PASS" \
    -H 'Content-Type: application/json' \
    -d "$json_payload" \
    "$API_URL"

echo "[SUCCESS] Status posted"
