#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -euo pipefail

CONFIG_FILE="/sensos/etc/access_point.conf"
LOG_FILE="/sensos/log/wifi_access_point.log"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Load configuration
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: Configuration file $CONFIG_FILE not found." | tee -a "$LOG_FILE"
    exit 1
fi

# Read values from config
SSID=$(awk -F' = ' '/^ssid/ {print $2}' "$CONFIG_FILE")
PASSWORD=$(awk -F' = ' '/^password/ {print $2}' "$CONFIG_FILE")
INTERFACE=$(awk -F' = ' '/^interface/ {print $2}' "$CONFIG_FILE")
POWER_SAVE=$(awk -F' = ' '/^power_save/ {print $2}' "$CONFIG_FILE")

# Validate WPA2 password
if [[ ${#PASSWORD} -lt 8 || ${#PASSWORD} -gt 63 ]]; then
    echo "ERROR: WPA2 password must be between 8 and 63 characters." | tee -a "$LOG_FILE"
    exit 1
fi

# Unblock WiFi if needed
if rfkill list wifi | grep -q "Soft blocked: yes"; then
    echo "WiFi is soft-blocked. Unblocking..." | tee -a "$LOG_FILE"
    sudo rfkill unblock wifi
    sleep 2
fi

# Create the hotspot
echo "Creating WiFi Access Point..." | tee -a "$LOG_FILE"
sudo nmcli device wifi hotspot \
    ${INTERFACE:+ifname "$INTERFACE"} \
    con-name sensosap \
    ssid "$SSID" \
    password "$PASSWORD" || {
    echo "ERROR: Failed to create hotspot." | tee -a "$LOG_FILE"
    exit 1
}

# Apply power saving mode if specified and interface is known
if [[ -n "${POWER_SAVE:-}" && -n "${INTERFACE:-}" ]]; then
    if [[ "$POWER_SAVE" == "true" ]]; then
        echo "Enabling power saving mode on $INTERFACE..." | tee -a "$LOG_FILE"
        sudo iw dev "$INTERFACE" set power_save on || echo "⚠️ Failed to enable power saving" | tee -a "$LOG_FILE"
    elif [[ "$POWER_SAVE" == "false" ]]; then
        echo "Disabling power saving mode on $INTERFACE..." | tee -a "$LOG_FILE"
        sudo iw dev "$INTERFACE" set power_save off || echo "⚠️ Failed to disable power saving" | tee -a "$LOG_FILE"
    fi
fi

# Start SSH
sudo systemctl daemon-reload
sudo systemctl enable ssh
sudo systemctl start ssh

echo "✅ WiFi Access Point started successfully." | tee -a "$LOG_FILE"
