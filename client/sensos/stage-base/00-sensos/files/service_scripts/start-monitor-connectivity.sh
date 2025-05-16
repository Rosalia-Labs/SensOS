#!/bin/bash
#
# monitor-connectivity.sh
#
# This script pings the WireGuard server internal IP from the network config.
# If unreachable for 24 hours, it restarts the WireGuard interface.
# If still unreachable for 7 days, the system reboots.
# It also pings WG_ENDPOINT_IP to distinguish between WG and broader connectivity issues.
#

LOG_DIR="/sensos/log"
LOGFILE="$LOG_DIR/connectivity_check.log"
SETTINGS_FILE="/sensos/etc/network.conf"

INTERVAL=3600                            # Ping interval in seconds (1 hour)
RESTART_NETWORK_THRESHOLD=$((24 * 3600)) # Restart networking after 24 hours
REBOOT_THRESHOLD=$((7 * 24 * 3600))      # Reboot after 7 days

mkdir -p "$LOG_DIR"

if [[ -f "$SETTINGS_FILE" ]]; then
    source "$SETTINGS_FILE"
else
    echo "ERROR: Cannot find $SETTINGS_FILE." | tee -a "$LOGFILE"
    exit 1
fi

if [[ "$CONNECTIVITY_PROFILE" == "offline" ]]; then
    log "📴 Connectivity profile is 'offline'; skipping connectivity monitoring."
    while true; do sleep 86400; done
fi

if [[ -z "$SERVER_WG_IP" || -z "$NETWORK_NAME" ]]; then
    echo "ERROR: SERVER_WG_IP or NETWORK_NAME is not set in $SETTINGS_FILE." | tee -a "$LOGFILE"
    exit 1
fi

WG_INTERFACE="$NETWORK_NAME"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOGFILE"
}

last_success=$(date +%s)

log "🔍 Starting connectivity monitoring to $SERVER_WG_IP via $WG_INTERFACE. Ping every $(($INTERVAL / 3600)) hour(s)."

while true; do
    current_time=$(date +%s)
    downtime=$((current_time - last_success))

    if ping -c 1 -W 2 "$SERVER_WG_IP" >/dev/null 2>&1; then
        last_success=$current_time
        log "✅ Server IP ($SERVER_WG_IP) is reachable."
    else
        log "❌ Server IP ($SERVER_WG_IP) is unreachable."

        if [[ -n "$WG_ENDPOINT_IP" ]]; then
            if ping -c 1 -W 2 "$WG_ENDPOINT_IP" >/dev/null 2>&1; then
                log "🌍 Endpoint IP ($WG_ENDPOINT_IP) is reachable → WireGuard tunnel is down, but external network is up."
            else
                log "🚫 Endpoint IP ($WG_ENDPOINT_IP) is also unreachable → Possible broader connectivity or NAT failure."
            fi
        fi

        if [[ "$downtime" -ge "$REBOOT_THRESHOLD" ]]; then
            log "🚨 No ping for $downtime seconds (>= $REBOOT_THRESHOLD). Rebooting system..."
            sudo /sbin/reboot
            exit 0
        elif [[ "$downtime" -ge "$RESTART_NETWORK_THRESHOLD" ]]; then
            log "⚠️ No ping for $downtime seconds (>= $RESTART_NETWORK_THRESHOLD). Restarting WireGuard..."

            log "🧪 Restarting wg-quick@$WG_INTERFACE..."
            sudo wg-quick down "$WG_INTERFACE"
            sleep 2
            sudo wg-quick up "$WG_INTERFACE"

            external_ip=$(ip -4 addr show "$WG_INTERFACE" | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
            log "🌐 Local WireGuard IP after restart: $external_ip"

            handshake=$(sudo wg show "$WG_INTERFACE" | grep latest | awk -F': ' '{print $2}')
            log "🤝 Latest handshake time (if any): $handshake"

            sleep 10

            if ping -c 1 -W 2 "$SERVER_WG_IP" >/dev/null 2>&1; then
                last_success=$(date +%s)
                log "✅ WireGuard recovered after restart."
            else
                log "❌ Still unreachable after WireGuard restart."
            fi
        fi
    fi

    sleep "$INTERVAL"
done
