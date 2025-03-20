#!/bin/bash

set -e

if [[ -z "$SENSOS_USER" ]]; then
    . /etc/environment
fi

if [[ -z "$SENSOS_USER" ]]; then
    echo "ERROR: SENSOS_USER is not set. Exiting."
    exit 1
fi

# Default flags for docker-compose
DETACH_FLAG=""
BUILD_FLAG=""

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  --no-detach                   Run in detached mode (adds -d)
  --rebuild-containers          Rebuild containers (adds --build)
  --help                        Show this help message
EOF
}

# Parse command-line options
while [[ "$#" -gt 0 ]]; do
    case "$1" in
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

# Directory where the docker-compose file (and .env file) reside
DOCKER_COMPOSE_DIR="/home/${SENSOS_USER}/docker"

echo "Changing working directory to ${DOCKER_COMPOSE_DIR}"
cd "$DOCKER_COMPOSE_DIR"

echo "Running: docker compose up $DETACH_FLAG $BUILD_FLAG"
COMPOSE_BAKE=true docker compose up $DETACH_FLAG $BUILD_FLAG
