#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

#
# monitor-connectivity.sh
#
# This script monitors WireGuard connectivity:
# - Restarts WG if down for 6 hours.
# - Restarts networking if down for 24 hours.
# - Reboots if down for 7 days.
#

LOG_DIR="/sensos/log"
LOGFILE="$LOG_DIR/connectivity_check.log"
SETTINGS_FILE="/sensos/etc/network.conf"

INTERVAL=3600 # Check every hour

RESTART_WG_INTERVALS=6
RESTART_NETWORK_INTERVALS=24
REBOOT_INTERVALS=$((7 * 24))

RESTART_WG_THRESHOLD=$((RESTART_WG_INTERVALS * INTERVAL))
RESTART_NETWORK_THRESHOLD=$((RESTART_NETWORK_INTERVALS * INTERVAL))
REBOOT_THRESHOLD=$((REBOOT_INTERVALS * INTERVAL))

mkdir -p "$LOG_DIR"

if ((RESTART_WG_THRESHOLD >= RESTART_NETWORK_THRESHOLD)) || ((RESTART_NETWORK_THRESHOLD >= REBOOT_THRESHOLD)); then
    echo "ERROR: Thresholds must satisfy WG < Network < Reboot. Current values:" | tee -a "$LOGFILE"
    echo "  RESTART_WG_THRESHOLD=$RESTART_WG_THRESHOLD" | tee -a "$LOGFILE"
    echo "  RESTART_NETWORK_THRESHOLD=$RESTART_NETWORK_THRESHOLD" | tee -a "$LOGFILE"
    echo "  REBOOT_THRESHOLD=$REBOOT_THRESHOLD" | tee -a "$LOGFILE"
    exit 1
fi

if [[ -f "$SETTINGS_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$SETTINGS_FILE"
else
    echo "ERROR: Cannot find $SETTINGS_FILE." | tee -a "$LOGFILE"
    exit 1
fi

if [[ "$CONNECTIVITY_MODE" != "always" ]]; then
    echo "📴 Connectivity mode is not 'always'; skipping connectivity monitoring." | tee -a "$LOGFILE"
    while true; do sleep 86400; done
fi

if [[ -z "$SERVER_WG_IP" || -z "$WG_ENDPOINT_IP" || -z "$NETWORK_NAME" ]]; then
    echo "ERROR: SERVER_WG_IP, WG_ENDPOINT_IP, or NETWORK_NAME is not set in $SETTINGS_FILE." | tee -a "$LOGFILE"
    exit 1
fi

WG_INTERFACE="$NETWORK_NAME"            # interface name (e.g., wg0)
WG_NM_CONN="${WIREGUARD_NM_CONN:-$NETWORK_NAME}"  # NM connection id (defaults to NETWORK_NAME)

NMCLI_BIN="$(command -v nmcli || true)"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOGFILE"
}

# Helper: restart WG via NetworkManager if available; otherwise fall back to wg-quick.
restart_wireguard() {
    if [[ -n "$NMCLI_BIN" ]]; then
        # Try to bounce the NM connection by ID; if not found, fall back.
        if nmcli -t -f NAME connection show | grep -Fxq "$WG_NM_CONN"; then
            log "🔄 Restarting WireGuard via NetworkManager connection '$WG_NM_CONN'..."
            sudo nmcli connection down "$WG_NM_CONN" && sleep 2
            sudo nmcli connection up "$WG_NM_CONN"
            return $?
        fi
    fi
    log "🔄 NetworkManager not managing WG (or nmcli unavailable). Falling back to wg-quick on '$WG_INTERFACE'..."
    sudo wg-quick down "$WG_INTERFACE" || true
    sleep 2
    sudo wg-quick up "$WG_INTERFACE"
}

# Helper: restart the networking stack using NetworkManager when present.
restart_networking_stack() {
    if systemctl list-unit-files | grep -q '^NetworkManager\.service'; then
        log "🔌 Restarting NetworkManager..."
        sudo systemctl restart NetworkManager
    else
        log "🔌 NetworkManager not present; restarting legacy 'networking' service..."
        sudo systemctl restart networking
    fi
}

last_success=$(date +%s)
log "🔍 Starting connectivity monitoring to $SERVER_WG_IP via $WG_INTERFACE. Ping every $(($INTERVAL / 3600)) hour(s)."

while true; do
    sleep "$INTERVAL"

    current_time=$(date +%s)
    downtime=$((current_time - last_success))
    wg_ok=false

    if ping -c 1 -W 2 "$WG_ENDPOINT_IP" >/dev/null 2>&1; then
        log "🌍 Endpoint IP ($WG_ENDPOINT_IP) is reachable."

        if ping -c 1 -W 2 "$SERVER_WG_IP" >/dev/null 2>&1; then
            log "✅ Server WireGuard IP ($SERVER_WG_IP) is reachable."
            last_success=$current_time
            wg_ok=true
        else
            log "❌ Server WireGuard IP ($SERVER_WG_IP) is unreachable → WG tunnel failure suspected."
        fi
    else
        log "🚫 Endpoint IP ($WG_ENDPOINT_IP) is unreachable → Broader network failure suspected."
    fi

    if [[ "$downtime" -ge "$REBOOT_THRESHOLD" ]]; then
        log "🚨 No connectivity for $downtime seconds (>= $REBOOT_THRESHOLD). Rebooting..."
        sudo /sbin/reboot
        exit 0
    elif [[ "$downtime" -ge "$RESTART_NETWORK_THRESHOLD" ]]; then
        restart_networking_stack
        sleep 10
    elif [[ "$downtime" -ge "$RESTART_WG_THRESHOLD" ]]; then
        restart_wireguard
        sleep 10
        if ping -c 1 -W 2 "$SERVER_WG_IP" >/dev/null 2>&1; then
            last_success=$(date +%s)
            log "✅ WireGuard recovered after restart."
        else
            log "❌ Still unreachable after WireGuard restart."
        fi
    fi
done
