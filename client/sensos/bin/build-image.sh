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

rm -rf $STAGE_DST
cp -a $STAGE_SRC $STAGE_DST

cd $PI_GEN_DIR
rm -rf ./deploy/

./build-docker.sh

rm -rf $STAGE_DST

exit 0
