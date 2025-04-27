#!/bin/bash
set -e

# If this script is in <base>/server/bin, then:
#   dirname "${BASH_SOURCE[0]}" → <base>/server/bin
#   /../docker                → <base>/server/docker
WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../docker" && pwd)"
cd "$WORK_DIR"

echo "Working directory: $(pwd)" # should print <base>/server/docker

# Define default values
DEFAULT_DB_PORT=5432
DEFAULT_API_PORT=8000
DEFAULT_WG_PORT=51820
DEFAULT_WG_SERVER_IP="127.0.0.1"
DEFAULT_SENSOS_REGISTRY_PORT=5000
DEFAULT_SENSOS_REGISTRY_USER="sensos"
DEFAULT_SENSOS_REGISTRY_PASSWORD="sensos"
DEFAULT_POSTGRES_PASSWORD="sensos"
DEFAULT_API_PASSWORD="sensos"
DEFAULT_INITIAL_NETWORK="sensos"
DEFAULT_EXPOSE_CONTAINERS="false"

# Print help message
print_help() {
    cat <<EOF
Usage: $0 [options]

Options:
  --db-port PORT           Set database port (default: $DEFAULT_DB_PORT)
  --api-port PORT          Set API port (default: $DEFAULT_API_PORT)
  --wg-network NAME        Set WireGuard network name (default: $DEFAULT_INITIAL_NETWORK)
  --wg-server-ip IP        Set WireGuard IP (default: $DEFAULT_WG_SERVER_IP)
  --wg-port PORT           Set WireGuard port (default: $DEFAULT_WG_PORT)
  --postgres-password PWD  Set PostgreSQL password (default: $DEFAULT_POSTGRES_PASSWORD)
  --api-password PWD       Set API password (default: $DEFAULT_API_PASSWORD)
  --registry-port PORT     Set registry port (default: $DEFAULT_SENSOS_REGISTRY_PORT)
  --registry-user USER     Set registry username (default: $DEFAULT_SENSOS_REGISTRY_USER)
  --registry-password PWD  Set registry password (default: $DEFAULT_SENSOS_REGISTRY_PASSWORD)
  --expose-containers      Add containers to WireGuard (default: $DEFAULT_EXPOSE_CONTAINERS)
  -h, --help               Show this help message
EOF
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
    --wg-server-ip)
        WG_SERVER_IP="$2"
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
    --expose-containers)
        EXPOSE_CONTAINERS="true"
        shift
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

# Set defaults if variables not provided
DB_PORT=${DB_PORT:-$DEFAULT_DB_PORT}
API_PORT=${API_PORT:-$DEFAULT_API_PORT}
INITIAL_NETWORK=${INITIAL_NETWORK:-$DEFAULT_INITIAL_NETWORK}
WG_PORT=${WG_PORT:-$DEFAULT_WG_PORT}
WG_SERVER_IP=${WG_SERVER_IP:-$DEFAULT_WG_SERVER_IP}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-$DEFAULT_POSTGRES_PASSWORD}
API_PASSWORD=${API_PASSWORD:-$DEFAULT_API_PASSWORD}
SENSOS_REGISTRY_PORT=${SENSOS_REGISTRY_PORT:-$DEFAULT_SENSOS_REGISTRY_PORT}
SENSOS_REGISTRY_USER=${SENSOS_REGISTRY_USER:-$DEFAULT_SENSOS_REGISTRY_USER}
SENSOS_REGISTRY_PASSWORD=${SENSOS_REGISTRY_PASSWORD:-$DEFAULT_SENSOS_REGISTRY_PASSWORD}
EXPOSE_CONTAINERS=${EXPOSE_CONTAINERS:-$DEFAULT_EXPOSE_CONTAINERS}

# Backup existing .env if it exists
if [ -f .env ]; then
    mv .env .env.bak
    chmod 600 .env.bak
    echo "✅ Current environment configuration backed up to .env.bak."
fi

# Write configuration to .env
cat >.env <<EOF
DB_PORT=$DB_PORT
API_PORT=$API_PORT
INITIAL_NETWORK=$INITIAL_NETWORK
WG_PORT=$WG_PORT
WG_SERVER_IP=$WG_SERVER_IP
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
API_PASSWORD=$API_PASSWORD
SENSOS_REGISTRY_PORT=$SENSOS_REGISTRY_PORT
SENSOS_REGISTRY_USER=$SENSOS_REGISTRY_USER
SENSOS_REGISTRY_PASSWORD=$SENSOS_REGISTRY_PASSWORD
EXPOSE_CONTAINERS=$EXPOSE_CONTAINERS
EOF

chmod 600 .env
echo "✅ Environment configuration written to .env."
echo "✅ Setup completed successfully."
