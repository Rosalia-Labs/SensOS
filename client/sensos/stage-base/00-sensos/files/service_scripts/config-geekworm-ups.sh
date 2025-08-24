#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -euo pipefail

EXPECTED_BOOT_UART="BOOT_UART=1"
EXPECTED_POWER_OFF="POWER_OFF_ON_HALT=1"
EXPECTED_PSU="PSU_MAX_CURRENT=5000"
BOOTCONF="/tmp/bootconf.txt"

log() {
    echo "[INFO] $*"
}

err() {
    echo "[ERROR] $*" >&2
    exit 1
}

# Check that rpi-eeprom-config exists
command -v rpi-eeprom-config >/dev/null || err "rpi-eeprom-config not found"

# Attempt to read current config
if ! CURRENT_CONF=$(sudo rpi-eeprom-config 2>/dev/null); then
    log "Could not read current EEPROM config. Forcing EEPROM update."
    FORCE_UPDATE=true
else
    # Check if all expected settings are present
    if echo "$CURRENT_CONF" | grep -q "$EXPECTED_BOOT_UART" &&
        echo "$CURRENT_CONF" | grep -q "$EXPECTED_POWER_OFF" &&
        echo "$CURRENT_CONF" | grep -q "$EXPECTED_PSU"; then
        log "EEPROM already configured as expected. Exiting."
        exit 0
    fi
    FORCE_UPDATE=false
fi

# Write new config
log "Creating EEPROM config override file..."
{
    echo '[all]'
    echo "$EXPECTED_BOOT_UART"
    echo "$EXPECTED_POWER_OFF"
    echo "$EXPECTED_PSU"
} >"$BOOTCONF"

log "Applying EEPROM configuration..."
if ! sudo rpi-eeprom-config --apply "$BOOTCONF"; then
    err "Failed to apply EEPROM config"
fi

log "EEPROM configuration applied. Verifying..."
sleep 2

# Re-check configuration
if ! NEW_CONF=$(sudo rpi-eeprom-config 2>/dev/null); then
    err "Failed to verify EEPROM config after applying."
fi

if echo "$NEW_CONF" | grep -q "$EXPECTED_BOOT_UART" &&
    echo "$NEW_CONF" | grep -q "$EXPECTED_POWER_OFF" &&
    echo "$NEW_CONF" | grep -q "$EXPECTED_PSU"; then
    log "EEPROM configuration verified successfully."
    exit 0
else
    err "EEPROM config mismatch after apply. Manual intervention may be required."
fi
