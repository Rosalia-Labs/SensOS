#!/bin/bash
set -euo pipefail

# Log start
echo "[gps-service] Starting GPS service at $(date)"

# One-time disable system gpsd services if active
if systemctl is-enabled gpsd.socket &>/dev/null || systemctl is-enabled gpsd.service &>/dev/null; then
    echo "[gps-service] Disabling system gpsd services"
    systemctl disable --now gpsd.socket gpsd.service || true
fi

# Optional: load I2C overlay if needed and not already present
if ! [ -e /dev/i2c-3 ]; then
    echo "[gps-service] Loading I2C GPIO overlay"
    dtoverlay -d /boot/firmware/overlays i2c-gpio bus=3 i2c_gpio_sda=23 i2c_gpio_scl=24 baudrate=10000
    sleep 1 # give time for /dev/i2c-3 to appear
fi

# Start gpsd if not already running
if ! pgrep -f "gpsd.*i2c-3" >/dev/null; then
    echo "[gps-service] Launching gpsd on /dev/i2c-3"
    gpsd -n /dev/i2c-3
else
    echo "[gps-service] gpsd is already running"
fi

# Block forever
echo "[gps-service] Running. Press Ctrl+C to exit."
tail -f /dev/null
