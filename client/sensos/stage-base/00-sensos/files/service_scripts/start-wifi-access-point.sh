#!/bin/bash

CONFIG_FILE="/sensos/etc/wifi_access_point.conf"
LOG_FILE="/sensos/log/wifi_access_point.log"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Load configuration
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: Configuration file $CONFIG_FILE not found." | tee -a "$LOG_FILE"
    exit 1
fi

SSID=$(awk -F' = ' '/^ssid/ {print $2}' "$CONFIG_FILE")
PASSWORD=$(awk -F' = ' '/^password/ {print $2}' "$CONFIG_FILE")
INTERFACE=$(awk -F' = ' '/^interface/ {print $2}' "$CONFIG_FILE")
BAND=$(awk -F' = ' '/^band/ {print $2}' "$CONFIG_FILE")
CHANNEL=$(awk -F' = ' '/^channel/ {print $2}' "$CONFIG_FILE")
COUNTRY_CODE=$(awk -F' = ' '/^country/ {print $2}' "$CONFIG_FILE")

LOW_TXPOWER=$(awk -F' = ' '/^low_txpower/ {print $2}' "$CONFIG_FILE")
POWER_SAVE=$(awk -F' = ' '/^power_save/ {print $2}' "$CONFIG_FILE")
LIMIT_WIDTH=$(awk -F' = ' '/^limit_width/ {print $2}' "$CONFIG_FILE")
BEACON_INTERVAL=$(awk -F' = ' '/^beacon_interval/ {print $2}' "$CONFIG_FILE")

# Ensure WPA2 password is valid
if [[ ${#PASSWORD} -lt 8 || ${#PASSWORD} -gt 63 ]]; then
    echo "ERROR: WPA2 password must be between 8 and 63 characters." | tee -a "$LOG_FILE"
    exit 1
fi

# Apply WiFi country code only if explicitly set in the config file
if [[ -n "$COUNTRY_CODE" ]]; then
    echo "Setting WiFi country code to $COUNTRY_CODE..." | tee -a "$LOG_FILE"
    sudo raspi-config nonint do_wifi_country "$COUNTRY_CODE"
fi

if rfkill list wifi | grep -q "Soft blocked: yes"; then
    echo "WiFi is soft-blocked. Unblocking..."
    sudo rfkill unblock wifi
    sleep 2
fi

# Remove any existing hotspot connection named "sensosap"
sudo nmcli connection delete sensosap 2>/dev/null || echo "No existing access point to delete." | tee -a "$LOG_FILE"

# Build and execute the hotspot command
echo "Creating WiFi Access Point..." | tee -a "$LOG_FILE"
sudo nmcli device wifi hotspot \
    ${INTERFACE:+ifname "$INTERFACE"} \
    con-name sensosap \
    ssid "$SSID" \
    password "$PASSWORD" \
    ${BAND:+band "$BAND"} \
    ${CHANNEL:+channel "$CHANNEL"} || {
    echo "ERROR: Failed to create hotspot." | tee -a "$LOG_FILE"
    exit 1
}

# The AP only makes sense with ssh running
sudo systemctl daemon-reload
sudo systemctl enable ssh
sudo systemctl start ssh

echo "✅ WiFi Access Point started successfully." | tee -a "$LOG_FILE"
