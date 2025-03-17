#!/bin/bash
set -e

# Use environment variables (with defaults if needed)
API_USERNAME=${API_USERNAME:-sensos}
API_PASSWORD=${API_PASSWORD}
WG_SERVER_IP=${WG_SERVER_IP} # This may be used as a fallback.
WG_PORT=51820

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
# For each network, check if a configuration file already exists.
curl -s -u "$API_USERNAME:$API_PASSWORD" -X GET "http://sensos-controller:8000/get-wireguard-network-names" | jq -r '.networks[]' | while read -r N_NAME; do
    echo "Setting up network ${N_NAME}..."

    LOCAL_WG_CONF="/etc/wireguard/${N_NAME}.conf"
    if [ -f "$LOCAL_WG_CONF" ]; then
        echo "Configuration for network ${N_NAME} already exists. Skipping key generation."
    else
        echo "No existing configuration for ${N_NAME} found. Proceeding with key generation."

        echo "Retrieving network information for '${N_NAME}'..."
        NETWORK_INFO=$(curl -s -u "$API_USERNAME:$API_PASSWORD" -X GET \
            "http://sensos-controller:8000/get-network-info?network_name=${N_NAME}")

        if [ -z "$NETWORK_INFO" ] || [ "$NETWORK_INFO" == "null" ]; then
            echo "Failed to retrieve network information. Exiting."
            exit 1
        fi

        WG_IP_RANGE=$(echo "$NETWORK_INFO" | jq -r '.ip_range')
        WG_SERVER_PUBLIC_IP=$(echo "$NETWORK_INFO" | jq -r '.wg_public_ip')
        WG_SERVER_PUBLIC_KEY=$(echo "$NETWORK_INFO" | jq -r '.wg_public_key')

        if [[ -z "$WG_IP_RANGE" || "$WG_IP_RANGE" == "null" ]]; then
            echo "Failed to retrieve network IP range. Exiting."
            exit 1
        fi

        if [[ -z "$WG_SERVER_PUBLIC_IP" || "$WG_SERVER_PUBLIC_IP" == "null" ]]; then
            echo "Failed to retrieve WireGuard server public IP. Exiting."
            exit 1
        fi

        if [[ -z "$WG_SERVER_PUBLIC_KEY" || "$WG_SERVER_PUBLIC_KEY" == "null" ]]; then
            echo "Failed to retrieve WireGuard server public key. Exiting."
            exit 1
        fi

        echo "Network IP range: $WG_IP_RANGE"
        echo "WireGuard server public IP: $WG_SERVER_PUBLIC_IP"
        echo "WireGuard server public key: $WG_SERVER_PUBLIC_KEY"

        # Extract the first two octets from the IP range.
        IFS='.' read -r -a IP_PARTS <<<"${WG_IP_RANGE%%/*}"
        WG_NETWORK_PREFIX="${IP_PARTS[0]}.${IP_PARTS[1]}"

        # Assign this container a fixed IP within the network.
        WG_IP="${WG_NETWORK_PREFIX}.0.1"
        echo "This container is assigned WireGuard IP: $WG_IP"

        echo "Generating client's WireGuard key pair..."
        CLIENT_PRIVATE_KEY=$(wg genkey)
        CLIENT_PUBLIC_KEY=$(echo "$CLIENT_PRIVATE_KEY" | wg pubkey)

        if [ -z "$CLIENT_PRIVATE_KEY" ] || [ -z "$CLIENT_PUBLIC_KEY" ]; then
            echo "Failed to generate WireGuard key pair."
            exit 1
        fi

        echo "Client public key: $CLIENT_PUBLIC_KEY"

        echo "Registering client's public key with the controller..."
        KEY_RESPONSE=$(curl -s -u "$API_USERNAME:$API_PASSWORD" -X POST \
            -H "Content-Type: application/json" \
            -d "{\"wg_ip\": \"$WG_IP\", \"wg_public_key\": \"$CLIENT_PUBLIC_KEY\"}" \
            http://sensos-controller:8000/register-wireguard-key)
        echo "Register WireGuard key response: $KEY_RESPONSE"

        echo "Looking up Docker IP for sensos-wireguard..."
        WG_DOCKER_IP=$(getent hosts sensos-wireguard | awk '{ print $1 }')
        if [ -z "$WG_DOCKER_IP" ]; then
            echo "Failed to determine the Docker IP of sensos-wireguard. Falling back to WG_SERVER_IP: $WG_SERVER_IP"
            WG_DOCKER_IP=${WG_SERVER_IP}
        else
            echo "Found sensos-wireguard Docker IP: $WG_DOCKER_IP"
        fi

        echo "Writing local WireGuard configuration to ${LOCAL_WG_CONF}..."
        cat >"${LOCAL_WG_CONF}" <<EOF
[Interface]
Address = ${WG_IP}/32
PrivateKey = ${CLIENT_PRIVATE_KEY}

[Peer]
PublicKey = ${WG_SERVER_PUBLIC_KEY}
Endpoint = ${WG_DOCKER_IP}:${WG_PORT}
AllowedIPs = ${WG_NETWORK_PREFIX}.0.0/16
PersistentKeepalive = 25
EOF
        chmod 600 "${LOCAL_WG_CONF}"
        echo "Local WireGuard configuration written successfully."
    fi

    # Bring up the WireGuard interface if not already active.
    if ip link show "${N_NAME}" >/dev/null 2>&1; then
        echo "WireGuard interface '${N_NAME}' is already up."
    else
        echo "Bringing up WireGuard interface for network '${N_NAME}'..."
        if ! wg-quick up "${N_NAME}"; then
            echo "Failed to start WireGuard interface. Exiting."
            exit 1
        fi
    fi
done

echo "Showing WireGuard config..."
wg

echo "Starting nginx..."
exec nginx -g "daemon off;"
