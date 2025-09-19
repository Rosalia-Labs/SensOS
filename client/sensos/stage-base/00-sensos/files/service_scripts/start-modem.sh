#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

CONFIG_FILE="/sensos/etc/modem.conf"
NETWORK_CONF="/sensos/etc/network.conf"
LOG_DIR="/sensos/log"
LOG_FILE="$LOG_DIR/modem.log"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Ensure modem configuration file exists
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Error: Modem configuration file $CONFIG_FILE not found." | tee -a "$LOG_FILE"
    exit 1
fi

# Load modem configuration
source "$CONFIG_FILE"

# Rotate log if > 5MB
if [[ -f "$LOG_FILE" && $(stat -c%s "$LOG_FILE") -gt 5000000 ]]; then
    mv "$LOG_FILE" "$LOG_FILE.$(date +'%Y-%m-%d_%H-%M-%S')"
fi

# Delete logs older than 30 days
find "$LOG_DIR" -name "modem.log.*" -mtime +30 -delete

# Check if nmcli is available
if ! command -v nmcli >/dev/null; then
    echo "Error: nmcli command not found. Please install NetworkManager." | tee -a "$LOG_FILE"
    exit 1
fi

# Function to restart the LTE modem if disconnected
restart_lte() {
    echo "Reconnecting LTE modem..." | tee -a "$LOG_FILE"
    sudo nmcli c up "lte" || {
        echo "Failed to bring up LTE connection. Deleting and recreating..." | tee -a "$LOG_FILE"
        sudo nmcli connection delete "lte" 2>/dev/null
        sudo nmcli c add type gsm ifname "$IFACE" con-name "lte" \
            connection.interface-name "$INTERNAL_NAME" gsm.apn "$APN" ipv4.method auto
        sudo nmcli c up "lte"
    }
}

while true; do
    CONNECTIVITY_MODE="always"
    if [[ -f "$NETWORK_CONF" ]]; then
        source "$NETWORK_CONF"
    fi

    STATUS=$(nmcli -t -f DEVICE,STATE device status | awk -F: -v d="$IFACE" '$1==d{print $2}')

    if [[ "$CONNECTIVITY_MODE" == "always" ]]; then
        if [[ "$STATUS" == "connected" ]]; then
            echo "LTE connection is up." | tee -a "$LOG_FILE"
        else
            restart_lte
        fi
    else
        echo "CONNECTIVITY_MODE=$CONNECTIVITY_MODE → bringing LTE down." | tee -a "$LOG_FILE"
        sudo nmcli con down "lte" >/dev/null 2>&1 || true
        sudo nmcli dev disconnect "$IFACE" >/dev/null 2>&1 || true
    fi

    sleep 300
done