#!/bin/bash
set -e

# Absolute paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PI_GEN_DIR="${CLIENT_DIR}/pi-gen"
BUILD_SCRIPT="${CLIENT_DIR}/bin/build-image.sh"
BASE_CONFIG_FILE="${PI_GEN_DIR}/config"
TEMP_CONFIG_FILE="${PI_GEN_DIR}/config.qemu"

FORCE_BULLSEYE=true
BUILD_DOCKER_IMAGES=false
CONTINUE_BUILD=false
REMOVE_DEPLOY=false

# Parse CLI args
while [[ $# -gt 0 ]]; do
    case "$1" in
    --build-docker-images)
        BUILD_DOCKER_IMAGES=true
        shift
        ;;
    --continue)
        CONTINUE_BUILD=true
        shift
        ;;
    --remove-existing-images)
        REMOVE_DEPLOY=true
        shift
        ;;
    *)
        echo "Unknown option: $1"
        exit 1
        ;;
    esac
done

# Sanity check
if [ ! -f "$BASE_CONFIG_FILE" ]; then
    echo "Base config not found at $BASE_CONFIG_FILE"
    exit 1
fi

# Create modified config
cp "$BASE_CONFIG_FILE" "$TEMP_CONFIG_FILE"
echo 'USE_QEMU=1' >>"$TEMP_CONFIG_FILE"

echo "Using QEMU config:"
cat "$TEMP_CONFIG_FILE"
echo

# Backup + override config
cp "$BASE_CONFIG_FILE" "${BASE_CONFIG_FILE}.bak"
cp "$TEMP_CONFIG_FILE" "$BASE_CONFIG_FILE"

# Always restore original config
cleanup() {
    echo "Restoring original config..."
    mv "${BASE_CONFIG_FILE}.bak" "$BASE_CONFIG_FILE"
    rm -f "$TEMP_CONFIG_FILE"
}
trap cleanup EXIT

# Run actual build
"$BUILD_SCRIPT" \
    $([ "$BUILD_DOCKER_IMAGES" = true ] && echo "--build-docker-images") \
    $([ "$CONTINUE_BUILD" = true ] && echo "--continue") \
    $([ "$REMOVE_DEPLOY" = true ] && echo "--remove-existing-images")

echo "✅ QEMU image build complete."
