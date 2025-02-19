#!/bin/sh

# Define default values
DEFAULT_DB_PORT=5432
DEFAULT_API_PORT=8000
DEFAULT_WG_PORT=51820
DEFAULT_POSTGRES_PASSWORD="sensos"

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
    --postgres-password=*)
        POSTGRES_PASSWORD="${1#*=}"
        ;;
    *)
        echo "Unknown option: $1"
        echo "Usage: $0 [--db-port=PORT] [--api-port=PORT] [--wg-port=PORT] [--postgres-password=PASSWORD]"
        exit 1
        ;;
    esac
    shift
done

# Use environment variables if set, otherwise use defaults
DB_PORT="${DB_PORT:-$DEFAULT_DB_PORT}"
API_PORT="${API_PORT:-$DEFAULT_API_PORT}"
WG_PORT="${WG_PORT:-$DEFAULT_WG_PORT}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$DEFAULT_POSTGRES_PASSWORD}"

# Output .env contents with defaults
echo "DB_PORT=$DB_PORT"
echo "API_PORT=$API_PORT"
echo "WG_PORT=$WG_PORT"
echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD"

# Dire warning if password is default
if [ "$POSTGRES_PASSWORD" = "$DEFAULT_POSTGRES_PASSWORD" ]; then
    {
        echo ""
        echo "🚨🚨🚨 WARNING: Using the default POSTGRES_PASSWORD! 🚨🚨🚨"
        echo "This is extremely insecure and could expose your database to unauthorized access."
        echo "Set a strong password in your .env file or environment variables immediately!"
        echo ""
    } 1>&2
fi
