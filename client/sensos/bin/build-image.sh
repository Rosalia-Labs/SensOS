#!/bin/bash
set -e

# Set directories
SENSOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PI_GEN_DIR="${SENSOS_DIR}/../pi-gen"
CONFIG_FILE="${PI_GEN_DIR}/config"
BUILD_DOCKER_IMAGES=false

STAGE_SRC="${SENSOS_DIR}/stage-base/00-sensos"
STAGE_DST="${PI_GEN_DIR}/stage2/04-sensos"

# Build control flags
CONTINUE_BUILD=false
REMOVE_DEPLOY=false

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "Options:"
    echo "  --remove-existing-images       Delete the 'deploy' directory before building"
    echo "  --build-docker-images          Build and store docker images for offline use"
    echo "  --continue                     Continue from a previously interrupted build"
    echo "  -h, --help                     Show this help message and exit"
    echo
    exit 0
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
    --remove-existing-images)
        REMOVE_DEPLOY=true
        shift
        ;;
    --build-docker-images)
        BUILD_DOCKER_IMAGES=true
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

# Get Git metadata
GIT_COMMIT=$(git -C "$SENSOS_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git -C "$SENSOS_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
GIT_TAG=$(git -C "$SENSOS_DIR" describe --tags --always 2>/dev/null || echo "unknown")
GIT_DIRTY=$(test -n "$(git -C "$SENSOS_DIR" status --porcelain 2>/dev/null)" && echo "true" || echo "false")

# Get version information from VERSION file
VERSION_FILE="$SENSOS_DIR/../VERSION"
if [ -f "$VERSION_FILE" ]; then
    VERSION_MAJOR=$(awk -F' = ' '/^major/ {print $2}' "$VERSION_FILE")
    VERSION_MINOR=$(awk -F' = ' '/^minor/ {print $2}' "$VERSION_FILE")
    VERSION_PATCH=$(awk -F' = ' '/^patch/ {print $2}' "$VERSION_FILE")
    VERSION_SUFFIX=$(awk -F' = ' '/^suffix/ {print $2}' "$VERSION_FILE")
else
    VERSION_MAJOR="unknown"
    VERSION_MINOR="unknown"
    VERSION_PATCH="unknown"
    VERSION_SUFFIX=""
fi

# Write version metadata into the custom stage
VERSION_FILE_PATH="${STAGE_SRC}/files/etc/sensos-version"
mkdir -p "$(dirname "$VERSION_FILE_PATH")"
cat >"$VERSION_FILE_PATH" <<EOF
VERSION=${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}-${VERSION_SUFFIX}
GIT_COMMIT=$GIT_COMMIT
GIT_BRANCH=$GIT_BRANCH
GIT_TAG=$GIT_TAG
GIT_DIRTY=$GIT_DIRTY
BUILD_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF

# Helper functions for cleanup
try_rm_rf() {
    local target="$1"
    rm -rf "$target" 2>/dev/null || sudo rm -rf "$target" 2>/dev/null
}

try_rm_rf_contents() {
    local target="$1"
    if [ -d "$target" ]; then
        rm -rf "${target:?}/"* 2>/dev/null || sudo rm -rf "${target:?}/"* 2>/dev/null
    fi
}

echo "Copying custom stage to pi-gen..."
try_rm_rf "$STAGE_DST"
cp -R "$STAGE_SRC" "$STAGE_DST"

cd "$PI_GEN_DIR"
pwd

if [ "$REMOVE_DEPLOY" = true ]; then
    try_rm_rf_contents ./deploy
fi

# -----------------------------------------------------------------------------
# Build images from each Dockerfile in the custom stage 'files' tree.
# -----------------------------------------------------------------------------

# Define target platform for the Pi (adjust if your Pi uses a different ARM variant)
TARGET_PLATFORM="linux/arm64"

if [ "$BUILD_DOCKER_IMAGES" = true ]; then
    echo "Finding all Dockerfiles..."
    DOCKERFILES=$(find "./stage2/04-sensos/files/docker" -name 'Dockerfile')
    for dockerfile in $DOCKERFILES; do
        context_dir=$(dirname "$dockerfile")
        # Generate an image tag from the directory name
        image_tag="sensos-client-$(basename "$context_dir" | tr '_' '-')"

        echo "Building image for Dockerfile at $dockerfile with tag $image_tag..."
        # Build the image for the Pi (ARM) platform using Docker Buildx
        docker buildx build --platform linux/arm64 -t $image_tag --load "$context_dir"

        # Save the built image as a gzip-compressed tarball in the same directory
        output_tarball="$context_dir/${image_tag}.tar.gz"
        echo "Saving image $image_tag to $output_tarball..."
        docker save $image_tag | gzip >"$output_tarball"
    done
else
    echo "[INFO] Skipping Docker image build as requested (--skip-build-images)"
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

# -----------------------------------------------------------------------------
# Cleanup copied stage
# -----------------------------------------------------------------------------
echo "Cleaning up copied stage..."
try_rm_rf "$STAGE_DST"

echo "Build complete. All images have been built and saved as tar.gz files alongside their Dockerfiles."
exit 0
