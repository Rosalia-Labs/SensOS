#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -e

source /sensos/lib/parse-switches.sh

CFG_FILE="/sensos/etc/network.conf"

CONNECTIVITY_MODE=""
WIREGUARD_IFACE=""
API_IP=""
NEEDS_TEARDOWN=0

# We’ll record which NM connections we explicitly brought up
BROUGHT_UP_CONNS=()

# ---------- Helpers ----------
nm_list_conns_by_type() {
    # Usage: nm_list_conns_by_type <type>
    # type: gsm | 802-3-ethernet | wifi | ...
    nmcli -t -f NAME,TYPE con show | awk -F: -v want="$1" '$2==want{print $1}'
}

nm_is_wifi_ap_mode() {
    # Returns 0 (true) if the given connection is Wi-Fi AP mode, else 1 (false)
    local name="$1"
    local t
    t="$(nmcli -t -f TYPE con show "$name" 2>/dev/null || true)"
    [[ "$t" != "wifi" ]] && return 1
    local mode
    mode="$(nmcli -g 802-11-wireless.mode con show "$name" 2>/dev/null || true)"
    [[ "$mode" == "ap" ]]
}

up_conn_safe() {
    local name="$1"
    [[ -z "$name" ]] && return 0
    # Do not *start* Wi-Fi APs here (avoid accidentally enabling hotspots)
    if nm_is_wifi_ap_mode "$name"; then
        echo "[INFO] Skipping Wi-Fi AP connection (not bringing up): $name"
        return 0
    fi
    if nmcli -t -f NAME con show | grep -qx "$name"; then
        echo "[INFO] Bringing up connection: $name"
        if nmcli -w 15 con up "$name"; then
            BROUGHT_UP_CONNS+=("$name")
        else
            echo "[WARN] Failed to bring up connection: $name"
        fi
    fi
}

down_conn_safe() {
    local name="$1"
    [[ -z "$name" ]] && return 0
    # NEVER bring down any Wi-Fi AP profile
    if nm_is_wifi_ap_mode "$name"; then
        echo "[INFO] Not bringing down Wi-Fi AP connection: $name"
        return 0
    fi
    echo "[INFO] Bringing down connection: $name"
    nmcli -w 10 con down "$name" || true
}

setup() {
    echo "[INFO] Enabling NetworkManager networking..."
    nmcli networking on || true

    # Bring up ALL GSM profiles
    while read -r gsm; do
        [[ -n "$gsm" ]] && up_conn_safe "$gsm"
    done < <(nm_list_conns_by_type gsm)

    # Bring up ALL Ethernet profiles
    while read -r wired; do
        [[ -n "$wired" ]] && up_conn_safe "$wired"
    done < <(nm_list_conns_by_type 802-3-ethernet)

    # Bring up ALL Wi-Fi *station* profiles (skip AP)
    while read -r wifi; do
        [[ -n "$wifi" ]] && up_conn_safe "$wifi"
    done < <(nm_list_conns_by_type wifi)

    # Brief connectivity check
    for i in {1..5}; do
        status="$(nmcli networking connectivity)"
        [[ "$status" == "full" ]] && break
        sleep 2
    done

    if [[ "$(nmcli networking connectivity)" != "full" ]]; then
        echo "[WARN] Did not achieve full networking (nmcli: $(nmcli networking connectivity))."
    fi

    echo "[INFO] Bringing up WireGuard interface ($WIREGUARD_IFACE)..."
    sudo systemctl start "wg-quick@${WIREGUARD_IFACE}.service"
}

cleanup() {
    if [[ $NEEDS_TEARDOWN -eq 1 && "$keep_after" != "true" ]]; then
        echo "[INFO] Stopping WireGuard interface ($WIREGUARD_IFACE)..."
        sudo systemctl stop "wg-quick@${WIREGUARD_IFACE}.service"

        echo "[INFO] Bringing down NM connections brought up by this script (excluding Wi-Fi APs)..."
        # Tear down in reverse order just in case
        for (( idx=${#BROUGHT_UP_CONNS[@]}-1 ; idx>=0 ; idx-- )); do
            down_conn_safe "${BROUGHT_UP_CONNS[$idx]}"
        done
    else
        echo "[INFO] Skipping teardown (keep-after is true or teardown not needed)."
    fi
}

trap cleanup EXIT

# ---------- Required config ----------
if [[ ! -f "$CFG_FILE" ]]; then
    echo "[FATAL] $CFG_FILE not found." >&2
    exit 1
fi

# Read required fields from network.conf
while IFS='=' read -r key value; do
    key="${key// /}"
    value="${value// /}"
    case "$key" in
        CONNECTIVITY_MODE) CONNECTIVITY_MODE="${value,,}" ;;
        CLIENT_WG_IP)      CLIENT_WG_IP="$value" ;;
        SERVER_WG_IP)      SERVER_WG_IP="$value" ;;
        NETWORK_NAME)      NETWORK_NAME="$value" ;;
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

# ---------- CLI options for the runner ----------
register_option "--keep-after" "keep_after" "Keep connectivity after running command" "false"
register_option "--interval"   "ping_interval" "Ping interval in seconds" "30"
register_option "--timeout"    "ping_timeout"  "API ping timeout in seconds" "3600"

parse_switches "$(basename "$0")" "$@"
set -- "${REMAINING_ARGS[@]}"

if [[ $# -eq 0 ]]; then
    echo "[FATAL] No subcommand provided to run after connectivity is up." >&2
    exit 1
fi

# Teardown unless always-on
if [[ "$CONNECTIVITY_MODE" != "always" ]]; then
    NEEDS_TEARDOWN=1
fi

API_PING_INTERVAL="${ping_interval:-30}"
API_PING_TIMEOUT="${ping_timeout:-3600}"
start_time=$(date +%s)

echo "[INFO] Trying to reach API proxy at $API_IP (timeout ${API_PING_TIMEOUT}s)..."

if ping -c1 -W2 "$API_IP" >/dev/null 2>&1; then
    echo "[INFO] Ping to $API_IP succeeded without bringing up connections."
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
        sleep "$API_PING_INTERVAL"
    done
fi

echo "[INFO] Running: $*"
"$@"
EXIT_CODE=$?

exit $EXIT_CODE
