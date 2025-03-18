#!/bin/bash
#
# monitor-connectivity.sh
#
# This script pings the WireGuard internal IP <prefix>.0.1 once per hour.
# If unreachable for 24 hours, networking is restarted.
# If unreachable for 7 days, the system is rebooted.
#

# Ensure SENSOS_USER is set
if [[ -z "$SENSOS_USER" ]]; then
    echo "ERROR: SENSOS_USER is not set in /etc/environment. Exiting."
    exit 1
fi

# Set home directory and paths
USER_HOME=$(eval echo ~$SENSOS_USER)
LOG_DIR="$USER_HOME/log"
LOGFILE="$LOG_DIR/connectivity_check.log"
SETTINGS_FILE="$USER_HOME/etc/network.conf"

# Ensure necessary directories exist
mkdir -p "$LOG_DIR"

# Load settings file if available
if [[ -f "$SETTINGS_FILE" ]]; then
    source "$SETTINGS_FILE"
fi

# Ensure WireGuard server IP is set
if [[ -z "$SERVER_WG_IP" ]]; then
    echo "ERROR: SERVER_WG_IP is not set in $SETTINGS_FILE." | tee -a "$LOGFILE"
    exit 1
fi

# Configuration variables
INTERVAL=3600                            # Ping interval in seconds (1 hour)
RESTART_NETWORK_THRESHOLD=$((24 * 3600)) # Restart networking after 24 hours
REBOOT_THRESHOLD=$((7 * 24 * 3600))      # Reboot after 7 days

# Function to log messages with timestamps.
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOGFILE"
}

# Initialize last successful ping time
last_success=$(date +%s)

log "Starting connectivity check to WireGuard server IP ($SERVER_WG_IP). Pings every $(($INTERVAL / 3600)) hour(s)."

while true; do
    current_time=$(date +%s)
    downtime=$((current_time - last_success))

    if ping -c 1 -W 2 "$SERVER_WG_IP" >/dev/null 2>&1; then
        last_success=$(date +%s)
        log "✅ WireGuard internal IP ($SERVER_WG_IP) is reachable."
    else
        log "❌ WireGuard internal IP ($SERVER_WG_IP) is unreachable."

        if [[ "$downtime" -ge "$REBOOT_THRESHOLD" ]]; then
            log "🚨 No successful ping for $downtime seconds (>= $REBOOT_THRESHOLD). Rebooting system..."
            /sbin/reboot
            exit 0 # In case reboot command fails
        elif [[ "$downtime" -ge "$RESTART_NETWORK_THRESHOLD" ]]; then
            log "⚠️ No successful ping for $downtime seconds (>= $RESTART_NETWORK_THRESHOLD). Restarting networking..."
            systemctl restart networking
        fi
    fi

    sleep "$INTERVAL"
done
