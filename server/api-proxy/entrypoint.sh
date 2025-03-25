#!/bin/bash
set -euo pipefail

# Use environment variables (with defaults if needed)
API_USERNAME=${API_USERNAME:-sensos}
API_PASSWORD=${API_PASSWORD}
WG_SERVER_IP=${WG_SERVER_IP}

if [ -z "$API_PASSWORD" ]; then
    echo "API_PASSWORD is not set. Exiting."
    exit 1
fi

if [ -z "$WG_SERVER_IP" ]; then
    echo "WG_SERVER_IP is not set. Exiting."
    exit 1
fi

echo "Waiting for sensos-controller to be available..."
until curl -s -u "$API_USERNAME:$API_PASSWORD" http://sensos-controller:8000/ >/dev/null; do
    echo "sensos-controller not available, retrying in 5 seconds..."
    sleep 5
done

echo "Retrieving network names..."
curl -s -u "$API_USERNAME:$API_PASSWORD" http://sensos-controller:8000/get-wireguard-network-names | jq -r '.networks[]' | while read -r N_NAME; do
    echo "Setting up network '${N_NAME}'..."

    LOCAL_WG_CONF="/etc/wireguard/${N_NAME}.conf"

    if [ -f "$LOCAL_WG_CONF" ]; then
        echo "✔️ Configuration already exists for ${N_NAME}. Skipping."
    else
        echo "📡 Fetching network info for '${N_NAME}'..."
        NETWORK_INFO=$(curl -s -u "$API_USERNAME:$API_PASSWORD" \
            "http://sensos-controller:8000/get-network-info?network_name=${N_NAME}")

        WG_IP_RANGE=$(echo "$NETWORK_INFO" | jq -r '.ip_range')
        WG_SERVER_PUBLIC_IP=$(echo "$NETWORK_INFO" | jq -r '.wg_public_ip')
        WG_SERVER_PUBLIC_KEY=$(echo "$NETWORK_INFO" | jq -r '.wg_public_key')

        if [[ -z "$WG_IP_RANGE" || -z "$WG_SERVER_PUBLIC_IP" || -z "$WG_SERVER_PUBLIC_KEY" ]]; then
            echo "❌ Incomplete network information. Exiting."
            exit 1
        fi

        IFS='.' read -r -a IP_PARTS <<<"${WG_IP_RANGE%%/*}"
        WG_NETWORK_PREFIX="${IP_PARTS[0]}.${IP_PARTS[1]}"
        WG_IP="${WG_NETWORK_PREFIX}.0.1"

        echo "🔐 Generating WireGuard key pair..."
        CLIENT_PRIVATE_KEY=$(wg genkey)
        CLIENT_PUBLIC_KEY=$(echo "$CLIENT_PRIVATE_KEY" | wg pubkey)

        echo "📤 Registering public key with controller..."
        curl -s -u "$API_USERNAME:$API_PASSWORD" -X POST \
            -H "Content-Type: application/json" \
            -d "{\"wg_ip\": \"$WG_IP\", \"wg_public_key\": \"$CLIENT_PUBLIC_KEY\"}" \
            http://sensos-controller:8000/register-wireguard-key

        echo "🌐 Looking up Docker IP for sensos-wireguard..."
        WG_DOCKER_IP=$(getent hosts sensos-wireguard | awk '{ print $1 }')
        WG_ENDPOINT="${WG_DOCKER_IP:-$WG_SERVER_IP}"

        echo "📝 Writing WireGuard config to ${LOCAL_WG_CONF}..."
        cat >"$LOCAL_WG_CONF" <<EOF
[Interface]
Address = ${WG_IP}/32
PrivateKey = ${CLIENT_PRIVATE_KEY}

[Peer]
PublicKey = ${WG_SERVER_PUBLIC_KEY}
Endpoint = ${WG_ENDPOINT}:51820
AllowedIPs = ${WG_NETWORK_PREFIX}.0.0/16
PersistentKeepalive = 25
EOF
        chmod 600 "$LOCAL_WG_CONF"
    fi

    if ip link show "${N_NAME}" &>/dev/null; then
        echo "🔄 Interface '${N_NAME}' is already active."
    else
        echo "🚀 Bringing up interface '${N_NAME}'..."
        wg-quick up "${N_NAME}" || echo "⚠️ Failed to bring up ${N_NAME}"
    fi
done

echo "🔍 Current WireGuard state:"
wg

echo "📦 Starting nginx..."
exec nginx -g "daemon off;"
