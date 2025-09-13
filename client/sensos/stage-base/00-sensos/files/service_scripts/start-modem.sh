#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -euo pipefail

CONFIG_FILE="/sensos/etc/modem.conf"
LOG_DIR="/sensos/log"
LOG_FILE="$LOG_DIR/modem.log"
PROFILE_NAME="sensos-lte"

mkdir -p "$LOG_DIR"

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

rotate_logs() {
  if [[ -f "$LOG_FILE" && $(stat -c%s "$LOG_FILE") -gt 5000000 ]]; then
    mv "$LOG_FILE" "$LOG_FILE.$(date +'%Y-%m-%d_%H-%M-%S')"
  fi
  find "$LOG_DIR" -name "modem.log.*" -mtime +30 -delete || true
}

fatal() {
  log "FATAL: $*"
  exit 1
}

# --- Preflight ---------------------------------------------------------------

rotate_logs

[[ -f "$CONFIG_FILE" ]] || fatal "Modem configuration file $CONFIG_FILE not found."
# shellcheck disable=SC1090
source "$CONFIG_FILE"

# Expect DEVICE and APN from config-modem
DEVICE="${DEVICE:-}"
APN="${APN:-}"
[[ -n "$DEVICE" ]] || fatal "DEVICE is not set in $CONFIG_FILE"
[[ -n "$APN"    ]] || fatal "APN is not set in $CONFIG_FILE"

command -v nmcli >/dev/null || fatal "nmcli not found. Please install NetworkManager."

# Ensure NetworkManager is running
if ! nmcli -t general status >/dev/null 2>&1; then
  log "NetworkManager not responding; attempting to start via systemctl..."
  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl start NetworkManager || fatal "Failed to start NetworkManager"
  fi
fi

# Verify that the target device exists
if ! nmcli -t -f DEVICE,TYPE device | awk -F: -v d="$DEVICE" '$1==d {found=1} END{exit !found}'; then
  nmcli -t -f DEVICE,TYPE device | sed 's/^/[NM devices] /' | tee -a "$LOG_FILE" || true
  fatal "DEVICE '$DEVICE' not present in NetworkManager."
fi

# --- Helpers ----------------------------------------------------------------

ensure_profile() {
  # Create or fix the $PROFILE_NAME NM connection bound to $DEVICE with APN=$APN
  if nmcli -t -f NAME con show | grep -Fxq "$PROFILE_NAME"; then
    # Profile exists; ensure it's bound to the right device and APN
    current_ifname="$(nmcli -g connection.interface-name connection show "$PROFILE_NAME" 2>/dev/null || true)"
    current_apn="$(nmcli -g gsm.apn connection show "$PROFILE_NAME" 2>/dev/null || true)"
    changed=0

    if [[ "$current_ifname" != "$DEVICE" ]]; then
      log "Updating $PROFILE_NAME: connection.interface-name=$DEVICE (was '$current_ifname')"
      sudo nmcli connection modify "$PROFILE_NAME" connection.interface-name "$DEVICE" || changed=1
      changed=1
    fi

    if [[ "$current_apn" != "$APN" ]]; then
      log "Updating $PROFILE_NAME: gsm.apn=$APN (was '$current_apn')"
      sudo nmcli connection modify "$PROFILE_NAME" gsm.apn "$APN" || changed=1
      changed=1
    fi

    if (( changed )); then
      # Make sure it's a GSM profile (some older creations might differ)
      sudo nmcli connection modify "$PROFILE_NAME" connection.type gsm || true
      # Allow IPv4 auto (DHCP/MBIM-managed)
      sudo nmcli connection modify "$PROFILE_NAME" ipv4.method auto || true
    fi
  else
    log "Creating $PROFILE_NAME for DEVICE=$DEVICE, APN=$APN"
    # Use ifname "*" but pin to device via connection.interface-name
    # (this is the supported way to bind a connection to a specific device)
    sudo nmcli connection add type gsm ifname "*" con-name "$PROFILE_NAME" gsm.apn "$APN" \
      connection.interface-name "$DEVICE" ipv4.method auto
  fi
}

device_state() {
  # Return STATE column for the DEVICE (disconnected|connecting|connected|unavailable|…)
  nmcli -t -f DEVICE,STATE device |
    awk -F: -v d="$DEVICE" '$1==d{print $2; found=1} END{if(!found) print "missing"}'
}

bring_up() {
  log "Bringing up $PROFILE_NAME on $DEVICE"
  # Make sure the device is managed and connected by NM
  sudo nmcli device connect "$DEVICE" || log "nmcli device connect $DEVICE returned non-zero; continuing"
  # Try to activate the bound profile
  if ! sudo nmcli -w 45 connection up "$PROFILE_NAME"; then
    log "Activation failed; deleting and recreating $PROFILE_NAME..."
    sudo nmcli connection delete "$PROFILE_NAME" 2>/dev/null || true
    ensure_profile
    sudo nmcli -w 45 connection up "$PROFILE_NAME"
  fi
}

# --- Main loop --------------------------------------------------------------

ensure_profile
bring_up

SLEEP_SECS=300

while true; do
  st="$(device_state)"
  case "$st" in
    connected)
      log "LTE device '$DEVICE' is connected."
      ;;
    connecting|configuring|preparing)
      log "LTE device '$DEVICE' is in state '$st' (still coming up)."
      ;;
    disconnected|unavailable|failed|missing|*)
      log "LTE device '$DEVICE' is '$st'; attempting reconnect..."
      ensure_profile
      bring_up
      ;;
  esac

  rotate_logs
  sleep "$SLEEP_SECS"
done
