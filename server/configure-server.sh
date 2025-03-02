#!/bin/bash

set -e

# Define default values
DEFAULT_DB_PORT=5432
DEFAULT_API_PORT=8000
DEFAULT_WG_PORT=51820
DEFAULT_WG_IP="127.0.0.1"
DEFAULT_SENSOS_REGISTRY_PORT=5000
DEFAULT_SENSOS_REGISTRY_USER="sensos"
DEFAULT_SENSOS_REGISTRY_PASSWORD="sensos"
DEFAULT_POSTGRES_PASSWORD="sensos"
DEFAULT_API_PASSWORD="sensos"
DEFAULT_INITIAL_NETWORK="sensos"

# Print help message
print_help() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --db-port PORT          Set database port (default: $DEFAULT_DB_PORT)"
    echo "  --api-port PORT         Set API port (default: $DEFAULT_API_PORT)"
    echo "  --wg-network NAME       Set WireGuard network name (default: $DEFAULT_INITIAL_NETWORK)"
    echo "  --wg-ip IP              Set WireGuard IP (default: $DEFAULT_WG_IP)"
    echo "  --wg-port PORT          Set WireGuard port (default: $DEFAULT_WG_PORT)"
    echo "  --postgres-password PWD Set PostgreSQL password (default: $DEFAULT_POSTGRES_PASSWORD)"
    echo "  --api-password PWD      Set API password (default: $DEFAULT_API_PASSWORD)"
    echo "  --registry-ip IP        Set registry IP (default: --wg-ip setting)"
    echo "  --registry-port PORT    Set registry port (default: $DEFAULT_SENSOS_REGISTRY_PORT)"
    echo "  --registry-user USER    Set registry username (default: $DEFAULT_SENSOS_REGISTRY_USER)"
    echo "  --registry-password PWD Set registry password (default: $DEFAULT_SENSOS_REGISTRY_PASSWORD)"
    echo "  -h, --help              Show this help message"
    exit 0
}

# Allow command-line overrides
while [[ $# -gt 0 ]]; do
    case "$1" in
    --db-port)
        DB_PORT="$2"
        shift 2
        ;;
    --api-port)
        API_PORT="$2"
        shift 2
        ;;
    --wg-network)
        INITIAL_NETWORK="$2"
        shift 2
        ;;
    --wg-port)
        WG_PORT="$2"
        shift 2
        ;;
    --wg-ip)
        WG_IP="$2"
        shift 2
        ;;
    --postgres-password)
        POSTGRES_PASSWORD="$2"
        shift 2
        ;;
    --api-password)
        API_PASSWORD="$2"
        shift 2
        ;;
    --registry-ip)
        SENSOS_REGISTRY_IP="$2"
        shift 2
        ;;
    --registry-port)
        SENSOS_REGISTRY_PORT="$2"
        shift 2
        ;;
    --registry-user)
        SENSOS_REGISTRY_USER="$2"
        shift 2
        ;;
    --registry-password)
        SENSOS_REGISTRY_PASSWORD="$2"
        shift 2
        ;;
    -h | --help)
        print_help
        ;;
    *)
        echo "Unknown option: $1" >&2
        print_help
        ;;
    esac
done

# Use environment variables if set, otherwise use defaults
DB_PORT="${DB_PORT:-$DEFAULT_DB_PORT}"
API_PORT="${API_PORT:-$DEFAULT_API_PORT}"
INITIAL_NETWORK="${INITIAL_NETWORK:-$DEFAULT_INITIAL_NETWORK}"
WG_PORT="${WG_PORT:-$DEFAULT_WG_PORT}"
WG_IP="${WG_IP:-$DEFAULT_WG_IP}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$DEFAULT_POSTGRES_PASSWORD}"
API_PASSWORD="${API_PASSWORD:-$DEFAULT_API_PASSWORD}"
SENSOS_REGISTRY_IP="${SENSOS_REGISTRY_IP:-$WG_IP}"
SENSOS_REGISTRY_PORT="${SENSOS_REGISTRY_PORT:-$DEFAULT_SENSOS_REGISTRY_PORT}"
SENSOS_REGISTRY_USER="${SENSOS_REGISTRY_USER:-$DEFAULT_SENSOS_REGISTRY_USER}"
SENSOS_REGISTRY_PASSWORD="${SENSOS_REGISTRY_PASSWORD:-$DEFAULT_SENSOS_REGISTRY_PASSWORD}"

# Write environment variables to .env file with strict permissions
cat >.env <<EOF
DB_PORT=$DB_PORT
API_PORT=$API_PORT
INITIAL_NETWORK=$INITIAL_NETWORK
WG_PORT=$WG_PORT
WG_IP=$WG_IP
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
API_PASSWORD=$API_PASSWORD
SENSOS_REGISTRY_IP=$SENSOS_REGISTRY_IP
SENSOS_REGISTRY_PORT=$SENSOS_REGISTRY_PORT
SENSOS_REGISTRY_USER=$SENSOS_REGISTRY_USER
SENSOS_REGISTRY_PASSWORD=$SENSOS_REGISTRY_PASSWORD
EOF

chmod 600 .env

echo "✅ Environment configuration written to .env."

AUTH_DIR="./.api_auth"
if [ ! -d "$AUTH_DIR" ]; then exit 1; fi
docker run --rm --entrypoint htpasswd httpd:2 -Bbn "$SENSOS_REGISTRY_USER" "$SENSOS_REGISTRY_PASSWORD" >"$AUTH_DIR"/htpasswd
chmod 600 "$AUTH_DIR"/htpasswd

echo "✅ htpasswd file created at "$AUTH_DIR"/htpasswd."

CERT_DIR="./.certs"
if [ ! -d "$CERT_DIR" ]; then exit 1; fi
# Generate TLS certificates using Docker if they do not exist
if [ ! -f "$CERT_DIR/domain.crt" ] || [ ! -f "$CERT_DIR/domain.key" ]; then
    echo "Generating self-signed TLS certificate and key using Docker..."
    docker run --rm -v "$CERT_DIR":/certs frapsoft/openssl req \
        -newkey rsa:4096 -nodes -sha256 \
        -keyout /certs/domain.key \
        -x509 -days 365 \
        -out /certs/domain.crt \
        -subj "/CN=${SENSOS_REGISTRY_IP}"
    chmod 600 "$CERT_DIR/domain.crt" "$CERT_DIR/domain.key"
    echo "✅ TLS certificate (certs/domain.crt) and key (certs/domain.key) generated."
else
    echo "TLS certificate and key already exist in $CERT_DIR."
fi

# Warnings for default passwords
for var in "SENSOS_REGISTRY_PASSWORD" "POSTGRES_PASSWORD" "API_PASSWORD"; do
    eval "value=\$$var"
    eval "default_value=\$DEFAULT_${var}"
    if [ "$value" = "$default_value" ]; then
        echo "" >&2
        echo "🚨🚨🚨 WARNING: Using the default $var! 🚨🚨🚨" >&2
        echo "This is extremely insecure and could expose your system to unauthorized access." >&2
        echo "Set a strong password in your configuration!" >&2
        echo "" >&2
    fi
done

echo "✅ Setup completed successfully."
