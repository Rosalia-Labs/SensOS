#!/bin/bash
set -e

source /sensos/lib/parse-switches.sh

CFG_FILE="/sensos/etc/network.conf"
CONNECTIVITY_MODE=""

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

register_option "--keep-after" "keep-after" "Keep connectivity after running command" "false"

parse_switches "$(basename "$0")" "$@"

set -- "${REMAINING_ARGS[@]}"

if [[ $# -eq 0 ]]; then
    echo "[FATAL] No subcommand provided to run after connectivity is up." >&2
    exit 1
fi

NEEDS_TEARDOWN=0

echo "[INFO] Bringing up WireGuard interface ($WIREGUARD_IFACE)..."
sudo systemctl start "wg-quick@${WIREGUARD_IFACE}.service"

if [[ "$CONNECTIVITY_MODE" != "always" ]]; then
    NEEDS_TEARDOWN=1
fi

API_PING_TIMEOUT=300
API_PING_INTERVAL=10
start_time=$(date +%s)

cleanup() {
    if [[ $NEEDS_TEARDOWN -eq 1 && "$keep_after" != "true" ]]; then
        echo "[INFO] Stopping WireGuard interface ($WIREGUARD_IFACE)..."
        sudo systemctl stop "wg-quick@${WIREGUARD_IFACE}.service"
    else
        echo "[INFO] Skipping teardown (keep-after is true or teardown not needed)."
    fi
}

trap cleanup EXIT

echo "[INFO] Trying to reach API proxy at $API_IP (timeout ${API_PING_TIMEOUT}s)..."

WG_RECOVERY_ATTEMPTED=0

while true; do
    if ping -c1 -W2 "$API_IP" >/dev/null 2>&1; then
        echo "[INFO] Ping to $API_IP succeeded."
        break
    fi

    now=$(date +%s)
    elapsed=$((now - start_time))
    if ((elapsed >= API_PING_TIMEOUT)); then
        echo "[ERROR] Could not reach $API_IP after ${API_PING_TIMEOUT}s." >&2
        if [[ $NEEDS_TEARDOWN -eq 1 ]]; then
            echo "[INFO] Stopping WireGuard interface ($WIREGUARD_IFACE)..."
            sudo systemctl stop "wg-quick@${WIREGUARD_IFACE}.service"
        fi
        exit 2
    fi

    if [[ $WG_RECOVERY_ATTEMPTED -eq 0 ]]; then
        echo "[WARN] Ping failed, attempting to restart WireGuard interface $WIREGUARD_IFACE..."
        sudo systemctl restart "wg-quick@${WIREGUARD_IFACE}.service"
        WG_RECOVERY_ATTEMPTED=1
        sleep 4
        continue
    fi

    echo "[WARN] Still no reply from $API_IP after ${elapsed}s, retrying..."
    sleep $API_PING_INTERVAL
done

echo "[INFO] Running: $*"
"$@"
EXIT_CODE=$?

exit $EXIT_CODE
