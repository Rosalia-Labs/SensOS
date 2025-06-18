#!/bin/bash
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

if ((RESTART_WG_THRESHOLD >= RESTART_NETWORK_THRESHOLD)) || ((RESTART_NETWORK_THRESHOLD >= REBOOT_THRESHOLD)); then
    echo "ERROR: Thresholds must satisfy WG < Network < Reboot. Current values:" | tee -a "$LOGFILE"
    echo "  RESTART_WG_THRESHOLD=$RESTART_WG_THRESHOLD" | tee -a "$LOGFILE"
    echo "  RESTART_NETWORK_THRESHOLD=$RESTART_NETWORK_THRESHOLD" | tee -a "$LOGFILE"
    echo "  REBOOT_THRESHOLD=$REBOOT_THRESHOLD" | tee -a "$LOGFILE"
    exit 1
fi

mkdir -p "$LOG_DIR"

if [[ -f "$SETTINGS_FILE" ]]; then
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

WG_INTERFACE="$NETWORK_NAME"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOGFILE"
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
        log "🔌 Restarting networking stack..."
        sudo systemctl restart networking
        sleep 10
    elif [[ "$downtime" -ge "$RESTART_WG_THRESHOLD" ]]; then
        log "🔄 Restarting WireGuard interface ($WG_INTERFACE)..."
        sudo wg-quick down "$WG_INTERFACE"
        sleep 2
        sudo wg-quick up "$WG_INTERFACE"
        sleep 10

        if ping -c 1 -W 2 "$SERVER_WG_IP" >/dev/null 2>&1; then
            last_success=$(date +%s)
            log "✅ WireGuard recovered after restart."
        else
            log "❌ Still unreachable after WireGuard restart."
        fi
    fi
done
