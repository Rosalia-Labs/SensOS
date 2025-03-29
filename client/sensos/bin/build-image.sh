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
DOWNLOAD_OS_PACKAGES=

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "Options:"
    echo "  --remove-existing   Delete the 'deploy' directory before building"
    echo "  --download-wheels[=force]   Download Python wheels for offline install"
    echo "  --download-os-packages[=force]  Download OS-level .deb packages used in Dockerfiles"
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
    --download-os-packages)
        if [[ -n "$2" && "$2" != --* ]]; then
            DOWNLOAD_OS_PACKAGES="$2"
            shift 2
        else
            DOWNLOAD_OS_PACKAGES="true"
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

if [ -n "$DOWNLOAD_WHEELS" != "false" ]; then
    echo "Finding all requirements.txt files..."
    REQUIREMENTS_LIST=$(find "$SENSOS_DIR" -name 'requirements.txt')
    mkdir -p "$WHEEL_DIR"

    for req in $REQUIREMENTS_LIST; do
        rel_path="${req#$SENSOS_DIR/}"
        rel_path_trimmed="${rel_path#stage-base/00-sensos/files/docker/}"
        target_subdir="$WHEEL_DIR/$(dirname "$rel_path_trimmed")"

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

if [ -n "$DOWNLOAD_OS_PACKAGES" != "false" ]; then
    OS_PACKAGE_DIR="${STAGE_SRC}/files/os_packages"

    if [[ "$DOWNLOAD_OS_PACKAGES" != "force" && -n "$(find "$OS_PACKAGE_DIR" -name '*.deb' 2>/dev/null)" ]]; then
        echo "✅ OS packages already exist in $OS_PACKAGE_DIR, skipping download."
    else
        echo "📦 Scanning Dockerfiles for APT packages..."
        DOCKERFILES=$(find "$SENSOS_DIR/stage-base/00-sensos/files/docker" -name 'Dockerfile')

        mkdir -p "$OS_PACKAGE_DIR"
        APT_PACKAGES=()

        for dockerfile in $DOCKERFILES; do
            echo "🔍 Scanning $dockerfile for APT packages..."

            contents=$(awk '{ if (sub(/\\$/, "")) { line = line $0 } else { print line $0; line = "" } }' "$dockerfile")

            matches=$(echo "$contents" |
                grep -Eo 'apt(-get)? install[^&|;]*|REQUIRED_DEBS="[^"]*"')

            while IFS= read -r line; do
                if [[ "$line" == *REQUIRED_DEBS* ]]; then
                    pkgs=$(echo "$line" | sed -E 's/.*REQUIRED_DEBS="([^"]*)".*/\1/')
                else
                    pkgs=$(echo "$line" |
                        sed -E 's/apt(-get)? install//; s/--no-install-recommends//g; s/-y//g' |
                        tr -s ' ')
                fi
                APT_PACKAGES+=($pkgs)
            done <<<"$matches"
        done
    fi

    UNIQUE_PACKAGES=$(echo "${APT_PACKAGES[@]}" | tr ' ' '\n' | sort -u | tr '\n' ' ')

    if [[ -z "$UNIQUE_PACKAGES" ]]; then
        echo "⚠️  No APT packages found in any Dockerfile. Skipping .deb download."
    else
        echo "⬇️ Downloading .deb packages to $OS_PACKAGE_DIR..."
        docker run --rm --platform linux/arm64 \
            -v "$OS_PACKAGE_DIR":/debs \
            arm64v8/debian:bookworm bash -c "
                apt-get update && \
                apt-get install -y --no-install-recommends \
                    apt-utils apt-file wget gnupg apt-transport-https ca-certificates && \
                apt-get install -y --no-install-recommends ${UNIQUE_PACKAGES} --download-only && \
                cp -a /var/cache/apt/archives/*.deb /debs
            "

        echo "✅ Downloaded .deb files stored in $OS_PACKAGE_DIR"
    fi
fi

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
