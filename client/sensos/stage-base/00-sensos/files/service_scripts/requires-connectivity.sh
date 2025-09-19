#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -e

source /sensos/lib/parse-switches.sh

CFG_FILE="/sensos/etc/network.conf"
MODEM_CONF="/sensos/etc/modem.conf"
WIFI_CONF="/sensos/etc/wifi.conf"

CONNECTIVITY_MODE=""
WIREGUARD_IFACE=""
API_IP=""
NEEDS_TEARDOWN=0

CANDIDATE_DEVS=()

[[ -f "$MODEM_CONF" ]] && source "$MODEM_CONF"

if [[ -f "$MODEM_CONF" ]]; then
    while IFS='=' read -r key value; do
        key="${key// /}"
        value="${value// /}"
        [[ "$key" == "INTERNAL_NAME" && -n "$value" ]] && CANDIDATE_DEVS+=("$value")
    done <"$MODEM_CONF"
fi

if [[ -f "$WIFI_CONF" ]]; then
    while IFS='=' read -r key value; do
        key="${key// /}"
        value="${value// /}"
        [[ "$key" == "IFACE" && -n "$value" ]] && CANDIDATE_DEVS+=("$value")
    done <"$WIFI_CONF"
fi

CANDIDATE_DEVS+=("eth0")

for_each_managed_dev() {
    for dev in "${CANDIDATE_DEVS[@]}"; do
        echo "$dev"
    done
}

setup() {
    echo "[INFO] Enabling all NM-managed networking..."
    sudo nmcli networking on

    for dev in $(for_each_managed_dev); do
        if [[ -n "$APN" && -n "$INTERNAL_NAME" && "$dev" == "$INTERNAL_NAME" ]]; then
            echo "[INFO] Bringing LTE up via NM profile (APN=$APN)…"
            if ! nmcli -t -f NAME,TYPE c show | awk -F: '$2=="gsm"{print $1}' | grep -qx "lte"; then
                sudo nmcli c add type gsm ifname "*" con-name "lte" \
                    connection.interface-name "$INTERNAL_NAME" gsm.apn "$APN" ipv4.method auto
            fi
            sudo nmcli c up "lte" || echo "[WARN] nmcli c up lte failed"
        else
            echo "[INFO] Connecting $dev…"
            sudo nmcli device connect "$dev" || true
        fi
    done
    
    for i in {1..5}; do
        status="$(nmcli networking connectivity)"
        if [[ "$status" == "full" ]]; then
            break
        fi
        sleep 2
    done

    if [[ "$(nmcli networking connectivity)" != "full" ]]; then
        echo "[WARN] Did not achieve full networking."
    fi

    echo "[INFO] Bringing up WireGuard interface ($WIREGUARD_IFACE)..."
    sudo systemctl start "wg-quick@${WIREGUARD_IFACE}.service"
}

cleanup() {
    if [[ $NEEDS_TEARDOWN -eq 1 && "$keep_after" != "true" ]]; then
        echo "[INFO] Stopping WireGuard interface ($WIREGUARD_IFACE)..."
        sudo systemctl stop "wg-quick@${WIREGUARD_IFACE}.service"
        echo "[INFO] Disconnecting managed interfaces..."
        for dev in $(for_each_managed_dev); do
            echo "[INFO] Disconnecting $dev..."
            sudo nmcli device disconnect "$dev" || true
        done
    else
        echo "[INFO] Skipping teardown (keep-after is true or teardown not needed)."
    fi
}

trap cleanup EXIT

if [[ ! -f "$CFG_FILE" ]]; then
    echo "[FATAL] $CFG_FILE not found." >&2
    exit 1
fi

while IFS='=' read -r key value; do
    key="${key// /}"
    value="${value// /}"
    case "$key" in
    CONNECTIVITY_MODE) CONNECTIVITY_MODE="${value,,}" ;;
    CLIENT_WG_IP) CLIENT_WG_IP="$value" ;;
    SERVER_WG_IP) SERVER_WG_IP="$value" ;;
    NETWORK_NAME) NETWORK_NAME="$value" ;;
    esac
done <"$CFG_FILE"

if [[ -z "$NETWORK_NAME" ]]; then
    echo "[FATAL] NETWORK_NAME (WireGuard interface) not set in $CFG_FILE." >&2
    exit 1
fi

if [[ -z "$CONNECTIVITY_MODE" ]]; then
    echo "[FATAL] CONNECTIVITY_MODE not set in $CFG_FILE." >&2
    exit 1
fi

if [[ "$CONNECTIVITY_MODE" == "offline" ]]; then
    echo "[INFO] Offline mode: skipping command."
    exit 0
fi

if [[ -z "$SERVER_WG_IP" ]]; then
    echo "[FATAL] SERVER_WG_IP not set in $CFG_FILE." >&2
    exit 1
fi

WIREGUARD_IFACE="$NETWORK_NAME"
API_IP="$SERVER_WG_IP"

register_option "--keep-after" "keep_after" "Keep connectivity after running command" "false"
register_option "--interval" "ping_interval" "Ping interval in seconds" "30"
register_option "--timeout" "ping_timeout" "API ping timeout in seconds" "3600"

parse_switches "$(basename "$0")" "$@"
set -- "${REMAINING_ARGS[@]}"

if [[ $# -eq 0 ]]; then
    echo "[FATAL] No subcommand provided to run after connectivity is up." >&2
    exit 1
fi

if [[ "$CONNECTIVITY_MODE" != "always" ]]; then
    NEEDS_TEARDOWN=1
fi

API_PING_INTERVAL="${ping_interval:-30}"
API_PING_TIMEOUT="${ping_timeout:-3600}"
start_time=$(date +%s)

echo "[INFO] Trying to reach API proxy at $API_IP (timeout ${API_PING_TIMEOUT}s)..."

if ping -c1 -W2 "$API_IP" >/dev/null 2>&1; then
    echo "[INFO] Ping to $API_IP succeeded without bringing up interfaces."
else
    setup
    while true; do
        if ping -c1 -W2 "$API_IP" >/dev/null 2>&1; then
            echo "[INFO] Ping to $API_IP succeeded after setup."
            break
        fi

        now=$(date +%s)
        elapsed=$((now - start_time))
        if ((elapsed >= API_PING_TIMEOUT)); then
            echo "[ERROR] Could not reach $API_IP after ${API_PING_TIMEOUT}s." >&2
            exit 2
        fi

        echo "[WARN] Still no reply from $API_IP after ${elapsed}s, retrying..."
        sleep $API_PING_INTERVAL
    done
fi

echo "[INFO] Running: $*"
"$@"
EXIT_CODE=$?

exit $EXIT_CODE
