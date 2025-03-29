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
    echo "  --download-wheels   Download Python wheels for offline install"
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
    --download-wheels)
        if [[ -n "$2" && "$2" != --* ]]; then
            DOWNLOAD_WHEELS="$2"
            shift 2
        else
            DOWNLOAD_WHEELS="true"
            shift
        fi
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

if [ -n "$DOWNLOAD_WHEELS" ]; then
    echo "Finding all requirements.txt files..."
    REQUIREMENTS_LIST=$(find "$SENSOS_DIR" -name 'requirements.txt')
    mkdir -p "$WHEEL_DIR"

    for req in $REQUIREMENTS_LIST; do
        rel_path="${req#$SENSOS_DIR/}"
        target_subdir="$WHEEL_DIR/$(dirname "$rel_path")"
        mkdir -p "$target_subdir"
        echo "📦 Checking wheels for: $rel_path → $target_subdir"

        wheel_count=$(ls "$target_subdir"/*.whl 2>/dev/null | wc -l)
        if [[ "$DOWNLOAD_WHEELS" != "force" && "$wheel_count" -gt 0 ]]; then
            echo "✅ Wheels already exist in $target_subdir, skipping."
            continue
        fi

        echo "⬇️ Downloading wheels..."
        docker run --rm \
            -v "$target_subdir":/out \
            -v "$(dirname "$req")":/src \
            arm64v8/python:3.11-slim \
            sh -c "pip install --upgrade pip && pip download -d /out -r /src/$(basename "$req")"
    done

    echo "✅ All wheels downloaded to $WHEEL_DIR/*"
fi

OS_PACKAGE_DIR="${STAGE_SRC}/files/os_packages"

echo "📦 Scanning Dockerfiles for APT packages..."
DOCKERFILES=$(find "$SENSOS_DIR" -name 'Dockerfile')

mkdir -p "$OS_PACKAGE_DIR"
APT_PACKAGES=()

for dockerfile in $DOCKERFILES; do
    echo "🔍 Scanning $dockerfile for APT packages..."

    # Read all lines and merge line continuations
    contents=$(sed ':a;N;$!ba;s/\\\n/ /g' "$dockerfile")

    # Extract lines with apt or apt-get install
    matches=$(echo "$contents" | grep -Eo 'apt(-get)? install[^&|;]*')

    while IFS= read -r line; do
        # Remove known flags and tokenize package names
        pkgs=$(echo "$line" |
            sed -E 's/apt(-get)? install//; s/--no-install-recommends//g; s/-y//g' |
            tr -s ' ')
        APT_PACKAGES+=($pkgs)
    done <<<"$matches"
done

# Remove duplicates and download using apt-get download
UNIQUE_PACKAGES=$(echo "${APT_PACKAGES[@]}" | tr ' ' '\n' | sort -u | tr '\n' ' ')

echo "⬇️ Downloading .deb packages to $OS_PACKAGE_DIR..."
docker run --rm --platform linux/arm64 \
    -v "$OS_PACKAGE_DIR":/debs \
    arm64v8/debian:bookworm bash -c "
        apt-get update && \
        apt-get install -y --no-install-recommends \
            apt-utils \
            apt-file \
            wget \
            gnupg \
            apt-transport-https \
            ca-certificates && \
        apt-get install -y --no-install-recommends ${UNIQUE_PACKAGES} --download-only && \
        cp -a /var/cache/apt/archives/*.deb /debs
"

echo "✅ Downloaded .deb files stored in $OS_PACKAGE_DIR"

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
