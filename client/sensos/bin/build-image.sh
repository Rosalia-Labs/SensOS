#!/bin/bash

set -e

SENSOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PI_GEN_DIR="${SENSOS_DIR}/../pi-gen"
CONFIG_FILE="${PI_GEN_DIR}/config"

STAGE_SRC="${SENSOS_DIR}/stage-base/00-sensos"
STAGE_DST="${PI_GEN_DIR}/stage2/04-sensos"
WHEEL_DIR="${STAGE_SRC}/files/python"

REMOVE_DEPLOY=false
CONTINUE_BUILD=false
DOWNLOAD_WHEELS=false

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "Options:"
    echo "  --remove-existing   Delete the 'deploy' directory before building"
    echo "  --continue          Continue from a previously interrupted build"
    echo "  --download-wheels   Download Python wheels for offline install"
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
    --download-wheels)
        DOWNLOAD_WHEELS=true
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

if [ "$DOWNLOAD_WHEELS" = true ]; then
    echo "Finding all requirements.txt files..."
    REQUIREMENTS_LIST=$(find "$SENSOS_DIR" -name 'requirements.txt')
    mkdir -p "$WHEEL_DIR"
    TMP_REQ="${WHEEL_DIR}/combined-requirements.txt"
    >"$TMP_REQ"

    for req in $REQUIREMENTS_LIST; do
        echo "Adding: $req"
        cat "$req" >>"$TMP_REQ"
    done

    echo "Removing duplicates..."
    sort -u "$TMP_REQ" -o "$TMP_REQ"

    echo "Downloading Python wheels for arm64..."
    docker run --rm -v "$WHEEL_DIR":/out arm64v8/python:3.11-slim \
        sh -c "pip install --upgrade pip && pip download -d /out -r /out/combined-requirements.txt"

    echo "✅ Wheels downloaded to $WHEEL_DIR"
fi

# Stage copy
echo "Copying custom stage to pi-gen..."
rm -rf "$STAGE_DST" || sudo rm -rf "$STAGE_DST"
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
rm -rf "$STAGE_DST" || sudo rm -rf "$STAGE_DST"

echo "Build complete."
exit 0
