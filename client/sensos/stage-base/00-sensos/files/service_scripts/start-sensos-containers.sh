#!/bin/bash

set -e

# Default flags for docker-compose
DETACH_FLAG="-d"
BUILD_FLAG=""

# Directory where the docker-compose file (and .env file) reside
DOCKER_COMPOSE_DIR="/sensos/docker"

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  --no-detach                   Run do not detach (removes -d)
  --rebuild-containers          Rebuild containers (adds --build)
  --help                        Show this help message
EOF
}

# Parse command-line options
while [[ "$#" -gt 0 ]]; do
    case "$1" in
    --no-detach)
        DETACH_FLAG=""
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

echo "Changing working directory to ${DOCKER_COMPOSE_DIR}"
cd "$DOCKER_COMPOSE_DIR"

echo "Running: docker compose up $DETACH_FLAG $BUILD_FLAG"
COMPOSE_BAKE=true docker compose up $DETACH_FLAG $BUILD_FLAG
