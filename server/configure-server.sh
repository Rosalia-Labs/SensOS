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
DEFAULT_NETWORK="sensos"

# Print help message
print_help() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  -d, --db-port PORT          Set database port (default: $DEFAULT_DB_PORT)"
    echo "  -a, --api-port PORT         Set API port (default: $DEFAULT_API_PORT)"
    echo "  -n, --wg-network NAME       Set WireGuard network name (default: $DEFAULT_NETWORK)"
    echo "  -w, --wg-port PORT         Set WireGuard port (default: $DEFAULT_WG_PORT)"
    echo "  -i, --wg-ip IP             Set WireGuard IP (default: $DEFAULT_WG_IP)"
    echo "  -p, --postgres-password PWD Set PostgreSQL password (default: $DEFAULT_POSTGRES_PASSWORD)"
    echo "  -x, --api-password PWD     Set API password (default: $DEFAULT_API_PASSWORD)"
    echo "  -r, --registry-port PORT   Set registry port (default: $DEFAULT_SENSOS_REGISTRY_PORT)"
    echo "  -u, --registry-user USER   Set registry username (default: $DEFAULT_SENSOS_REGISTRY_USER)"
    echo "  -s, --registry-password PWD Set registry password (default: $DEFAULT_SENSOS_REGISTRY_PASSWORD)"
    echo "  -h, --help                 Show this help message"
    exit 0
}

# Allow command-line overrides
while [[ $# -gt 0 ]]; do
    case "$1" in
    -d | --db-port)
        DB_PORT="$2"
        shift 2
        ;;
    -a | --api-port)
        API_PORT="$2"
        shift 2
        ;;
    -n | --wg-network)
        NETWORK="$2"
        shift 2
        ;;
    -w | --wg-port)
        WG_PORT="$2"
        shift 2
        ;;
    -i | --wg-ip)
        WG_IP="$2"
        shift 2
        ;;
    -p | --postgres-password)
        POSTGRES_PASSWORD="$2"
        shift 2
        ;;
    -x | --api-password)
        API_PASSWORD="$2"
        shift 2
        ;;
    -r | --registry-port)
        SENSOS_REGISTRY_PORT="$2"
        shift 2
        ;;
    -u | --registry-user)
        SENSOS_REGISTRY_USER="$2"
        shift 2
        ;;
    -s | --registry-password)
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
NETWORK="${NETWORK:-$DEFAULT_NETWORK}"
WG_PORT="${WG_PORT:-$DEFAULT_WG_PORT}"
WG_IP="${WG_IP:-$DEFAULT_WG_IP}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$DEFAULT_POSTGRES_PASSWORD}"
API_PASSWORD="${API_PASSWORD:-$DEFAULT_API_PASSWORD}"
REGISTRY_PORT="${SENSOS_REGISTRY_PORT:-$DEFAULT_SENSOS_REGISTRY_PORT}"
SENSOS_REGISTRY_USER="${SENSOS_REGISTRY_USER:-$DEFAULT_SENSOS_REGISTRY_USER}"
SENSOS_REGISTRY_PASSWORD="${SENSOS_REGISTRY_PASSWORD:-$DEFAULT_SENSOS_REGISTRY_PASSWORD}"

# Write environment variables to .env file with strict permissions
cat >.env <<EOF
DB_PORT=$DB_PORT
API_PORT=$API_PORT
NETWORK=$NETWORK
WG_PORT=$WG_PORT
WG_IP=$WG_IP
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
API_PASSWORD=$API_PASSWORD
SENSOS_REGISTRY_PORT=$SENSOS_REGISTRY_PORT
SENSOS_REGISTRY_USER=$SENSOS_REGISTRY_USER
SENSOS_REGISTRY_PASSWORD=$SENSOS_REGISTRY_PASSWORD
EOF

chmod 600 .env

echo "✅ Environment configuration written to .env."

# Create .htpasswd file for the registry authentication
mkdir -p .htauth
docker run --rm --entrypoint htpasswd httpd:2 -Bbn "$REGISTRY_USER" "$REGISTRY_PASSWORD" >.htauth/htpasswd
chmod 600 .htauth/htpasswd

echo "✅ htpasswd file created at .htauth/htpasswd."

# Warnings for default passwords
for var in "REGISTRY_PASSWORD" "POSTGRES_PASSWORD" "API_PASSWORD"; do
    eval "value=\$$var"
    eval "default_value=\$DEFAULT_$var"
    if [ "$value" = "$default_value" ]; then
        echo "" >&2
        echo "🚨🚨🚨 WARNING: Using the default $var! 🚨🚨🚨" >&2
        echo "This is extremely insecure and could expose your system to unauthorized access." >&2
        echo "Set a strong password in your configuration!" >&2
        echo "" >&2
    fi
done

echo "✅ Setup completed successfully."
