#!/bin/bash

set -e

SENSOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PI_GEN_DIR="${SENSOS_DIR}/../pi-gen"
CONFIG_FILE="${PI_GEN_DIR}/config"

STAGE_SRC="${SENSOS_DIR}/stage-base/00-sensos"
STAGE_DST="${PI_GEN_DIR}/stage2/04-sensos"

REMOVE_DEPLOY=false
CONTINUE_BUILD=false

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "Options:"
    echo "  --remove-existing   Delete the 'deploy' directory before building"
    echo "  --continue          Continue from a previously interrupted build"
    echo "  -h, --help          Show this help message and exit"
    echo
    exit 0
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
    --remove-existing)
        REMOVE_DEPLOY=true
        shift
        ;;
    --continue)
        CONTINUE_BUILD=true
        shift
        ;;
    -h | --help)
        usage
        ;;
    *)
        echo "Unknown option: $1"
        usage
        ;;
    esac
done

# Check configuration file
if [ ! -f "$CONFIG_FILE" ]; then
    echo "No configuration file found at $CONFIG_FILE."
    echo "Run generate-config.sh first."
    exit 1
fi

echo
echo "Building image using config:"
cat "$CONFIG_FILE"
echo

# Stage copy
echo "Copying custom stage to pi-gen..."
sudo rm -rf "$STAGE_DST"
cp -R "$STAGE_SRC" "$STAGE_DST"

cd "$PI_GEN_DIR"

if [ "$REMOVE_DEPLOY" = true ]; then
    echo "Removing existing deploy directory..."
    rm -rf ./deploy/
else
    echo "Keeping existing deploy directory."
fi

# Build image
if [ "$CONTINUE_BUILD" = true ]; then
    echo "Continuing previous build..."
    CONTINUE=1 ./build-docker.sh
else
    echo "Starting fresh build..."
    docker rm -v pigen_work 2>/dev/null || true
    ./build-docker.sh
fi

# Cleanup
echo "Cleaning up copied stage..."
sudo rm -rf "$STAGE_DST"

echo "Build complete."
exit 0
