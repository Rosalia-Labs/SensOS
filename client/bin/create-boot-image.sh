#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -euo pipefail

SENSOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../sensos" && pwd)"
PI_GEN_DIR="${SENSOS_DIR}/../pi-gen"
CONFIG_FILE="${PI_GEN_DIR}/config"
BUILD_DOCKER_IMAGES=false

STAGE_SRC="${SENSOS_DIR}/stage-base/00-sensos"
STAGE_DST="${PI_GEN_DIR}/stage2/04-sensos"

CONTINUE_BUILD=false
REMOVE_DEPLOY=false
USE_OVERLAY=true     # default behavior (previous behavior)
COPIED_OVERLAY=false # internal marker to decide end-of-run cleanup

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "Options:"
    echo "  --remove-existing              Delete previously created boot images in the deploy directory"
    echo "  --build-docker-images          Build and store docker images for offline use (ignored with --no-overlay)"
    echo "  --continue                     Continue from a previously interrupted build"
    echo "  --no-overlay                   Disable SensOS overlay; build stock pi-gen only"
    echo "  -h, --help                     Show this help message and exit"
    echo
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
    --remove-existing)
        REMOVE_DEPLOY=true; shift ;;
    --build-docker-images)
        BUILD_DOCKER_IMAGES=true; shift ;;
    --continue)
        CONTINUE_BUILD=true; shift ;;
    --no-overlay)
        USE_OVERLAY=false; shift ;;
    -h|--help)
        usage ;;
    *)
        echo "Unknown option: $1"
        usage ;;
    esac
done

# --- strict existence checks (no implicit creation) ---
if [[ ! -d "$SENSOS_DIR" ]]; then
    echo "Error: required directory not found: SENSOS_DIR ($SENSOS_DIR)" >&2
    exit 1
fi
if [[ ! -d "$PI_GEN_DIR" ]]; then
    echo "Error: required directory not found: PI_GEN_DIR ($PI_GEN_DIR)" >&2
    exit 1
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Error: configuration file not found: $CONFIG_FILE" >&2
    echo "Run generate-config.sh first." >&2
    exit 1
fi
if [[ "$USE_OVERLAY" == true ]]; then
    if [[ ! -d "$STAGE_SRC" ]]; then
        echo "Error: required overlay stage not found: $STAGE_SRC" >&2
        exit 1
    fi
fi

# Ensure pi-gen is checked out at a tag, not a dev branch or random commit.
CURRENT_PI_GEN_TAG=$(git -C "$PI_GEN_DIR" describe --exact-match --tags 2>/dev/null || true)
if [[ -z "$CURRENT_PI_GEN_TAG" ]]; then
    echo "ERROR: pi-gen is not checked out at a release tag."
    echo "Please checkout a tagged release in pi-gen (e.g., git checkout <tag>)."
    echo "Current pi-gen HEAD: $(git -C "$PI_GEN_DIR" rev-parse --short HEAD)"
    exit 1
else
    echo "pi-gen is on tag: $CURRENT_PI_GEN_TAG"
fi

echo
echo "Building image using config:"
cat "$CONFIG_FILE"
echo

# If we’re building a vanilla image, ensure no leftover overlay remains in pi-gen
if [[ "$USE_OVERLAY" == false ]]; then
    echo "Vanilla build requested: disabling overlay and Docker image packing"
    BUILD_DOCKER_IMAGES=false
    if [[ -e "$STAGE_DST" ]]; then
        echo "Removing previously present overlay at $STAGE_DST to ensure a stock build..."
        # Best effort removal; fail fast if it persists
        rm -rf "$STAGE_DST" 2>/dev/null || sudo rm -rf "$STAGE_DST" 2>/dev/null
        if [[ -e "$STAGE_DST" ]]; then
            echo "Error: failed to remove existing overlay at: $STAGE_DST" >&2
            exit 1
        fi
    fi
fi

# Gather version metadata ONLY when overlay is enabled (we won't touch overlay files otherwise)
if [[ "$USE_OVERLAY" == true ]]; then
    GIT_COMMIT=$(git -C "$SENSOS_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
    GIT_BRANCH=$(git -C "$SENSOS_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    GIT_TAG=$(git -C "$SENSOS_DIR" describe --tags --always 2>/dev/null || echo "unknown")
    GIT_DIRTY=$(test -n "$(git -C "$SENSOS_DIR" status --porcelain 2>/dev/null)" && echo "true" || echo "false")

    VERSION_FILE="$SENSOS_DIR/../../VERSION"
    if [[ -f "$VERSION_FILE" ]]; then
        VERSION_MAJOR=$(awk -F' = ' '/^\[version\]/{in=1;next} in && $1~/^major$/{print $2}' "$VERSION_FILE")
        VERSION_MINOR=$(awk -F' = ' '/^\[version\]/{in=1;next} in && $1~/^minor$/{print $2}' "$VERSION_FILE")
        VERSION_PATCH=$(awk -F' = ' '/^\[version\]/{in=1;next} in && $1~/^patch$/{print $2}' "$VERSION_FILE")
        VERSION_SUFFIX=$(awk -F' = ' '/^\[version\]/{in=1;next} in && $1~/^suffix$/{print $2}' "$VERSION_FILE")
    else
        VERSION_MAJOR="unknown"
        VERSION_MINOR="unknown"
        VERSION_PATCH="unknown"
        VERSION_SUFFIX=""
    fi

    VERSION="${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}"
    if [[ -n "${VERSION_SUFFIX:-}" ]]; then
        VERSION="${VERSION}-${VERSION_SUFFIX}"
    fi

    VERSION_FILE_PATH="${STAGE_SRC}/files/VERSION"
    VERSION_DIR="$(dirname "$VERSION_FILE_PATH")"
    if [[ ! -d "$VERSION_DIR" ]]; then
        echo "Error: required directory for VERSION not found: $VERSION_DIR" >&2
        echo "Refusing to create directories implicitly." >&2
        exit 1
    fi
    cat >"$VERSION_FILE_PATH" <<EOF
VERSION=$VERSION
GIT_COMMIT=$GIT_COMMIT
GIT_BRANCH=$GIT_BRANCH
GIT_TAG=$GIT_TAG
GIT_DIRTY=$GIT_DIRTY
BUILD_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF
fi

try_rm_rf() {
    local target="$1"
    rm -rf "$target" 2>/dev/null || sudo rm -rf "$target" 2>/dev/null
}

try_rm_rf_contents() {
    local target="$1"
    if [[ -d "$target" ]]; then
        rm -rf "${target:?}/"* 2>/dev/null || sudo rm -rf "${target:?}/"* 2>/dev/null
    fi
}

# Copy overlay only if enabled
if [[ "$USE_OVERLAY" == true ]]; then
    echo "Copying custom stage to pi-gen..."
    try_rm_rf "$STAGE_DST"
    cp -R "$STAGE_SRC" "$STAGE_DST"
    COPIED_OVERLAY=true
fi

cd "$PI_GEN_DIR"
pwd

if [[ "$REMOVE_DEPLOY" == true ]]; then
    echo "Removing previous deploy artifacts..."
    try_rm_rf_contents ./deploy
fi

TARGET_PLATFORM="linux/arm64"

# Optional: build and pack Docker images residing in the overlay (ignored in vanilla mode)
if [[ "$BUILD_DOCKER_IMAGES" == true ]]; then
    echo "Finding all Dockerfiles..."
    DOCKERFILES=$(find "./stage2/04-sensos/files/docker" -name 'Dockerfile')
    for dockerfile in $DOCKERFILES; do
        context_dir=$(dirname "$dockerfile")
        image_tag="sensos-client-$(basename "$context_dir" | tr '_' '-')"

        echo "Building image for Dockerfile at $dockerfile with tag $image_tag..."
        docker buildx build --platform linux/arm64 -t "$image_tag" --load "$context_dir"

        output_tarball="$context_dir/${image_tag}.tar.gz"
        echo "Saving image $image_tag to $output_tarball..."
        docker save "$image_tag" | gzip >"$output_tarball"
    done
fi

if [[ "$CONTINUE_BUILD" == true ]]; then
    echo "Continuing previous build..."
    CONTINUE=1 ./build-docker.sh
else
    echo "Starting fresh build..."
    docker rm -v pigen_work 2>/dev/null || true
    ./build-docker.sh
fi

# Clean up the overlay we copied in THIS run; leave stock tree untouched for vanilla
if [[ "$COPIED_OVERLAY" == true ]]; then
    echo "Cleaning up copied stage..."
    try_rm_rf "$STAGE_DST"
fi

echo "Build complete."
exit 0
