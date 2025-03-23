#!/bin/bash
# This script checks for a WiFi hotspot and re-establishes it if not running.
SSID="sensosnet"
PASSWORD="sensossensos"
while true; do
    # Check if the hotspot connection exists and is active.
    if ! nmcli -f NAME,TYPE,STATE connection show --active | grep -q "wifi.*Hotspot"; then
        echo "Hotspot not active. Bringing it up..."
        nmcli device wifi hotspot ssid "$SSID" password "$PASSWORD"
    fi
    sleep 10
done
