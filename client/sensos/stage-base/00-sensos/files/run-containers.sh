#!/bin/bash
set -e

# Default values for environment variables used in docker-compose.yml
POSTGRES_DB="postgres"
POSTGRES_USER="postgres"
POSTGRES_PASSWORD="sensos"
DB_HOST="sensos-client-database"
DB_PORT="5432"
AUDIO_SOURCE="record"
RECORD_DURATION="0"

# Flags for docker-compose
DETACH_FLAG=""
BUILD_FLAG=""

# Directory where the docker-compose file resides
DOCKER_COMPOSE_DIR="/usr/local/share/sensos"

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  --postgres-db <value>         Set POSTGRES_DB (default: postgres)
  --postgres-user <value>       Set POSTGRES_USER (default: postgres)
  --postgres-password <value>   Set POSTGRES_PASSWORD (default: sensos)
  --db-host <value>             Set DB_HOST (default: sensos-client-database)
  --db-port <value>             Set DB_PORT (default: 5432)
  --audio-source <value>        Set AUDIO_SOURCE (default: record)
  --record-duration <value>     Set RECORD_DURATION (default: 0)
  --no-detach                   Run in detached mode (adds -d)
  --rebuild-containers          Rebuild containers (adds --build)
  --help                        Show this help message
EOF
}

# Parse command-line options
while [[ "$#" -gt 0 ]]; do
    case "$1" in
    --postgres-db)
        POSTGRES_DB="$2"
        shift 2
        ;;
    --postgres-user)
        POSTGRES_USER="$2"
        shift 2
        ;;
    --postgres-password)
        POSTGRES_PASSWORD="$2"
        shift 2
        ;;
    --db-host)
        DB_HOST="$2"
        shift 2
        ;;
    --db-port)
        DB_PORT="$2"
        shift 2
        ;;
    --audio-source)
        AUDIO_SOURCE="$2"
        shift 2
        ;;
    --record-duration)
        RECORD_DURATION="$2"
        shift 2
        ;;
    --no-detach)
        DETACH_FLAG="-d"
        shift
        ;;
    --rebuild-containers)
        BUILD_FLAG="--build"
        shift
        ;;
    --help)
        usage
        exit 0
        ;;
    *)
        echo "Unknown option: $1"
        usage
        exit 1
        ;;
    esac
done

# Export environment variables for docker-compose interpolation
export POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DB_HOST DB_PORT AUDIO_SOURCE RECORD_DURATION

echo "Exported environment variables:"
echo "  POSTGRES_DB=$POSTGRES_DB"
echo "  POSTGRES_USER=$POSTGRES_USER"
echo "  POSTGRES_PASSWORD=$POSTGRES_PASSWORD"
echo "  DB_HOST=$DB_HOST"
echo "  DB_PORT=$DB_PORT"
echo "  AUDIO_SOURCE=$AUDIO_SOURCE"
echo "  RECORD_DURATION=$RECORD_DURATION"
echo ""

# Change to the directory containing the docker-compose file
echo "Changing working directory to ${DOCKER_COMPOSE_DIR}"
cd "$DOCKER_COMPOSE_DIR"

echo "Running: docker-compose up $DETACH_FLAG $BUILD_FLAG"
docker-compose up $DETACH_FLAG $BUILD_FLAG
