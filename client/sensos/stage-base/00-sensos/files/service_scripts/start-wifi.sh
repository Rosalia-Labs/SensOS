#!/bin/bash

CONFIG_FILE="/sensos/etc/wifi.conf"
LOG_DIR="/sensos/log"
LOG_FILE="$LOG_DIR/wifi.log"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Ensure wifi configuration file exists
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Error: WiFi configuration file $CONFIG_FILE not found." | tee -a "$LOG_FILE"
    exit 1
fi

# Load wifi configuration
source "$CONFIG_FILE"

# Rotate log if > 5MB
if [[ -f "$LOG_FILE" && $(stat -c%s "$LOG_FILE") -gt 5000000 ]]; then
    mv "$LOG_FILE" "$LOG_FILE.$(date +'%Y-%m-%d_%H-%M-%S')"
fi

# Delete logs older than 30 days
find "$LOG_DIR" -name "wifi.log.*" -mtime +30 -delete

# Check if nmcli is available
if ! command -v nmcli >/dev/null; then
    echo "Error: nmcli command not found. Please install NetworkManager." | tee -a "$LOG_FILE"
    exit 1
fi

# Function to restart WiFi if disconnected
restart_wifi() {
    echo "Connecting to WiFi network '$SSID'..." | tee -a "$LOG_FILE"
    sudo nmcli device disconnect "$IFACE" 2>/dev/null || true
    sudo nmcli connection delete "wifi-$SSID" 2>/dev/null || true
    sudo nmcli dev wifi connect "$SSID" password "$PASSWORD" ifname "$IFACE" name "wifi-$SSID" || {
        echo "Failed to connect to WiFi network '$SSID'" | tee -a "$LOG_FILE"
    }
}

# Main loop to monitor and restart WiFi connection
while true; do
    STATUS=$(nmcli -t -f DEVICE,STATE,CONNECTION dev | grep "^$IFACE:" | awk -F: '{print $2,$3}')

    if [[ "$STATUS" =~ "connected" && "$STATUS" =~ "$SSID" ]]; then
        echo "WiFi connection to '$SSID' is up." | tee -a "$LOG_FILE"
    else
        restart_wifi
    fi

    sleep 300
done
