#!/bin/bash
#
# monitor-connectivity.sh
#
# This script pings a designated server once per hour and tracks the
# time since the last successful ping. If the server doesn't respond
# for 24 hours, the script will restart networking. If there is no
# successful ping for 7 days, it will reboot the system.
#
# Configuration variables:
INTERVAL=3600                            # Ping interval in seconds (1 hour)
RESTART_NETWORK_THRESHOLD=$((24 * 3600)) # 24 hours in seconds
REBOOT_THRESHOLD=$((7 * 24 * 3600))      # 7 days in seconds
LOGFILE="/var/log/connectivity_check.log"

# Function to log messages with timestamps.
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >>"$LOGFILE"
}

# Initialize the last successful ping time to the current time.
last_success=$(date +%s)

log "Starting connectivity check to ${SERVER_IP} (pings every $(($INTERVAL / 3600)) hour(s))."

while true; do
    if ping -c 1 -W 2 "$SERVER_IP" >/dev/null 2>&1; then
        last_success=$(date +%s)
        log "Ping successful."
    else
        log "Ping failed."
    fi

    current_time=$(date +%s)
    downtime=$((current_time - last_success))

    if [ "$downtime" -ge "$REBOOT_THRESHOLD" ]; then
        log "No successful ping for $downtime seconds (>= $REBOOT_THRESHOLD). Rebooting system..."
        /sbin/reboot
        exit 0 # In case reboot command fails
    elif [ "$downtime" -ge "$RESTART_NETWORK_THRESHOLD" ]; then
        log "No successful ping for $downtime seconds (>= $RESTART_NETWORK_THRESHOLD). Restarting networking..."
        systemctl restart networking
    fi

    sleep "$INTERVAL"
done
