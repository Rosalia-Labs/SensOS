#!/bin/sh

set -e

# Define default values
DEFAULT_DB_PORT=5432
DEFAULT_API_PORT=8000
DEFAULT_WG_PORT=51820
DEFAULT_WG_IP="127.0.0.1"
DEFAULT_REGISTRY_PORT=5000
DEFAULT_REGISTRY_USER="sensos"
DEFAULT_REGISTRY_PASSWORD="sensos"
DEFAULT_POSTGRES_PASSWORD="sensos"
DEFAULT_API_PASSWORD="sensos"

# Allow command-line overrides
while [ $# -gt 0 ]; do
    case "$1" in
    --db-port=*)
        DB_PORT="${1#*=}"
        ;;
    --api-port=*)
        API_PORT="${1#*=}"
        ;;
    --wg-port=*)
        WG_PORT="${1#*=}"
        ;;
    --wg-ip=*)
        WG_IP="${1#*=}"
        ;;
    --postgres-password=*)
        POSTGRES_PASSWORD="${1#*=}"
        ;;
    --api-password=*)
        API_PASSWORD="${1#*=}"
        ;;
    --registry-port=*)
        REGISTRY_PORT="${1#*=}"
        ;;
    --registry-user=*)
        REGISTRY_USER="${1#*=}"
        ;;
    --registry-password=*)
        REGISTRY_PASSWORD="${1#*=}"
        ;;
    *)
        echo "Unknown option: $1" >&2
        echo "Usage: $0 [--db-port=PORT] [--api-port=PORT] [--wg-port=PORT] [--wg-ip=IP] [--postgres-password=PASSWORD] [--api-password=PASSWORD] [--registry-port=PORT] [--registry-user=USER] [--registry-password=PASSWORD]" >&2
        exit 1
        ;;
    esac
    shift
done

# Use environment variables if set, otherwise use defaults
DB_PORT="${DB_PORT:-$DEFAULT_DB_PORT}"
API_PORT="${API_PORT:-$DEFAULT_API_PORT}"
WG_PORT="${WG_PORT:-$DEFAULT_WG_PORT}"
WG_IP="${WG_IP:-$DEFAULT_WG_IP}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$DEFAULT_POSTGRES_PASSWORD}"
API_PASSWORD="${API_PASSWORD:-$DEFAULT_API_PASSWORD}"
REGISTRY_PORT="${REGISTRY_PORT:-$DEFAULT_REGISTRY_PORT}"
REGISTRY_USER="${REGISTRY_USER:-$DEFAULT_REGISTRY_USER}"
REGISTRY_PASSWORD="${REGISTRY_PASSWORD:-$DEFAULT_REGISTRY_PASSWORD}"

# Write environment variables to .env file with strict permissions
ENV_FILE=".env"
cat >"$ENV_FILE" <<EOF
DB_PORT=$DB_PORT
API_PORT=$API_PORT
WG_PORT=$WG_PORT
WG_IP=$WG_IP
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
API_PASSWORD=$API_PASSWORD
REGISTRY_PORT=$REGISTRY_PORT
REGISTRY_USER=$REGISTRY_USER
REGISTRY_PASSWORD=$REGISTRY_PASSWORD
EOF
chmod 600 "$ENV_FILE"

echo "✅ Environment configuration written to $ENV_FILE."

# Generate .htpasswd file using a transient httpd container
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
