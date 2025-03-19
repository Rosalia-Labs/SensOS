#!/bin/bash

# Use systemd environment variable
if [[ -z "$SENSOS_USER" ]]; then
    echo "ERROR: SENSOS_USER is not set. Exiting."
    exit 1
fi

USER_HOME=$(eval echo ~$SENSOS_USER)
CONFIG_FILE="$USER_HOME/etc/wifi_access_point.conf"
LOG_FILE="$USER_HOME/log/wifi_access_point.log"

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

# Apply WiFi country code correctly (Raspberry Pi OS)
if [[ -n "$COUNTRY_CODE" ]]; then
    echo "Setting WiFi country code to $COUNTRY_CODE..." | tee -a "$LOG_FILE"
    sudo raspi-config nonint do_wifi_country "$COUNTRY_CODE"
fi

# Remove any existing hotspot connection named "accesspoint"
nmcli connection delete accesspoint 2>/dev/null || echo "No existing access point to delete." | tee -a "$LOG_FILE"

# Build and execute the hotspot command
echo "Creating WiFi Access Point..." | tee -a "$LOG_FILE"
nmcli device wifi hotspot \
    ifname "$INTERFACE" \
    con-name accesspoint \
    ssid "$SSID" \
    password "$PASSWORD" \
    ${BAND:+band "$BAND"} \
    ${CHANNEL:+channel "$CHANNEL"} || {
    echo "ERROR: Failed to create hotspot." | tee -a "$LOG_FILE"
    exit 1
}

# Apply additional settings
[[ "$LOW_TXPOWER" == "true" ]] && iwconfig "$INTERFACE" txpower 5
[[ "$POWER_SAVE" == "true" ]] && iw dev "$INTERFACE" set power_save on
[[ "$LIMIT_WIDTH" == "true" ]] && nmcli connection modify accesspoint 802-11-wireless.channel-width 20
if [[ "$BEACON_INTERVAL" =~ ^[0-9]+$ ]]; then
    nmcli connection modify accesspoint 802-11-wireless.beacon-interval "$BEACON_INTERVAL"
fi

# Activate the access point
nmcli connection up accesspoint || {
    echo "ERROR: Failed to bring up access point." | tee -a "$LOG_FILE"
    exit 1
}

# Assign an additional hostname
sudo nmcli connection modify accesspoint ipv4.dns-search device.local
echo "WiFi Access Point started successfully. Now resolvable as 'device.local' on local network." | tee -a "$LOG_FILE"
