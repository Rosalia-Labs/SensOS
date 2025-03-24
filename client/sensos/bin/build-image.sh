#!/bin/bash

SENSOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PI_GEN_DIR="${SENSOS_DIR}/../pi-gen/"
CONFIG_FILE="${PI_GEN_DIR}/config"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "No configuration file found. Run generate-config.sh."
    exit 1
fi

echo
echo "Building image using config:"
cat "$CONFIG_FILE"
echo

STAGE_SRC="${SENSOS_DIR}/stage-base/00-sensos"
STAGE_DST="${PI_GEN_DIR}/stage2/04-sensos"

sudo rm -rf "$STAGE_DST"
cp -R "$STAGE_SRC" "$STAGE_DST"

cd "$PI_GEN_DIR"

# Default is to not remove the deploy directory.
REMOVE_DEPLOY=false

# Parse command-line arguments.
for arg in "$@"; do
    if [ "$arg" = "--remove-existing" ]; then
        REMOVE_DEPLOY=true
    fi
done

if [ "$REMOVE_DEPLOY" = true ]; then
    echo "Removing deploy directory..."
    rm -rf ./deploy/
else
    echo "Skipping removal of deploy directory."
fi

# Read and export variables in config file
set -a
source "$CONFIG_FILE"
set +a

./build-docker.sh

# Some weird security shit happening
sudo rm -rf "$STAGE_DST"

exit 0
