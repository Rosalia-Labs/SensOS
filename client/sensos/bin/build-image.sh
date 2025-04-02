#!/bin/bash

set -e

SENSOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PI_GEN_DIR="${SENSOS_DIR}/../pi-gen"
CONFIG_FILE="${PI_GEN_DIR}/config"

STAGE_SRC="${SENSOS_DIR}/stage-base/00-sensos"
STAGE_DST="${PI_GEN_DIR}/stage2/04-sensos"

REMOVE_DEPLOY=false
CONTINUE_BUILD=false
DOWNLOAD_WHEELS=false
DOWNLOAD_OS_PACKAGES=

CLEAN_WHEELS=false
CLEAN_OS_PACKAGES=false

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

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "Options:"
    echo "  --remove-existing-images   Delete the 'deploy' directory before building"
    echo "  --download-wheels[=force]   Download Python wheels for offline install"
    echo "  --download-os-packages[=force]  Download OS-level .deb packages used in Dockerfiles"
    echo "  --clean-os-packages  Remove downloaded debian packages from repository"
    echo "  --clean-wheels      Remove downloaded python wheels from repository"
    echo "  --clean             Same as --remove-existing-images --clean-wheels --clean-os-packages"
    echo "  --continue          Continue from a previously interrupted build"
    echo "  -h, --help          Show this help message and exit"
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
    --clean-wheels)
        CLEAN_WHEELS=true
        shift
        ;;
    --clean-os-packages)
        CLEAN_OS_PACKAGES=true
        shift
        ;;
    --continue)
        CONTINUE_BUILD=true
        shift
        ;;
    --clean)
        REMOVE_DEPLOY=true
        CLEAN_WHEELS=true
        CLEAN_OS_PACKAGES=true
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

# 🧼 Clean early
if [ "$CLEAN_WHEELS" = true ]; then
    echo "🧹 Cleaning downloaded Python wheels..."
    find "$STAGE_SRC/files/docker" -type f -name '*.whl' -exec rm -f {} +
fi

if [ "$CLEAN_OS_PACKAGES" = true ]; then
    echo "🧹 Cleaning downloaded OS .deb packages..."
    find "$STAGE_SRC/files/docker" -type f -name '*.deb' -exec rm -f {} +
fi

echo "Finding all requirements.txt files..."
REQUIREMENTS_LIST=$(find "$SENSOS_DIR" -name 'requirements.txt')
for req in $REQUIREMENTS_LIST; do
    req_dir=$(dirname "$req")
    wheels_dir="$req_dir/wheels"
    mkdir -p "$wheels_dir"
done

if [ "$DOWNLOAD_WHEELS" = "true" ] || [ "$DOWNLOAD_WHEELS" = "force" ]; then
    for req in $REQUIREMENTS_LIST; do
        req_dir=$(dirname "$req")
        wheels_dir="$req_dir/wheels"
        rel_path="${req#$SENSOS_DIR/}"
        echo "📦 Checking wheels for: $rel_path → $wheels_dir"

        wheel_count=$(ls "$wheels_dir"/*.whl 2>/dev/null | wc -l)
        if [[ "$DOWNLOAD_WHEELS" != "force" && "$wheel_count" -gt 0 ]]; then
            echo "✅ Wheels already exist in $wheels_dir, skipping."
            continue
        fi

        echo "⬇️ Downloading wheels..."
        docker run --rm \
            -v "$wheels_dir":/out \
            -v "$req_dir":/src \
            arm64v8/python:3.11-slim \
            sh -c "pip install --upgrade pip && pip download -d /out -r /src/$(basename "$req")"
    done

    echo "✅ All wheels downloaded."
fi

echo "Finding all Dockerfiles..."
DOCKERFILES=$(find "$SENSOS_DIR/stage-base/00-sensos/files/docker" -name 'Dockerfile')
for dockerfile in $DOCKERFILES; do
    pkg_dir=$(dirname "$dockerfile")
    os_pkg_dir="$pkg_dir/os_packages"
    mkdir -p "$os_pkg_dir"
done

if [ "$DOWNLOAD_OS_PACKAGES" = "true" ] || [ "$DOWNLOAD_OS_PACKAGES" = "force" ]; then
    for dockerfile in $DOCKERFILES; do
        pkg_dir=$(dirname "$dockerfile")
        os_pkg_dir="$pkg_dir/os_packages"
        echo "Processing OS packages for directory: $pkg_dir"

        # If not forcing and .deb files already exist, skip download.
        if [[ "$DOWNLOAD_OS_PACKAGES" != "force" && -n "$(find "$os_pkg_dir" -name '*.deb' 2>/dev/null)" ]]; then
            echo "✅ OS packages already exist in $os_pkg_dir, skipping download."
        else
            echo "🔍 Scanning $dockerfile for APT packages..."
            contents=$(awk '{ if (sub(/\\$/, "")) { line = line $0 } else { print line $0; line = "" } }' "$dockerfile")
            APT_PACKAGES=()

            matches=$(echo "$contents" | grep -E '^ENV[[:space:]]+REQUIRED_DEBS=')
            while IFS= read -r line; do
                pkgs=$(echo "$line" | sed -E 's/^ENV[[:space:]]+REQUIRED_DEBS="([^"]*)".*/\1/')
                APT_PACKAGES+=($pkgs)
            done <<<"$matches"

            echo "⬇️ Downloading .deb packages with full dependencies to $os_pkg_dir..."

            # Extract base image from the Dockerfile
            BASE_IMAGE=$(awk '/^FROM / { print $2; exit }' "$dockerfile")

            if [[ -z "$BASE_IMAGE" ]]; then
                echo "❌ Could not determine base image from $dockerfile"
                continue
            fi

            docker run --rm --platform linux/arm64 \
                -v "$os_pkg_dir":/debs \
                "$BASE_IMAGE" bash -c "\
                apt-get update && \
                apt-get install --reinstall --download-only -y --no-install-recommends \
                    apt-utils apt-file wget gnupg apt-transport-https ca-certificates && \
                apt-get install --reinstall --download-only -y --no-install-recommends ${APT_PACKAGES[*]} && \
                cp -a /var/cache/apt/archives/*.deb /debs"
        fi

        echo "📦 Generating local APT repo metadata in $os_pkg_dir..."
        docker run --rm -v "$os_pkg_dir":/debs "$BASE_IMAGE" \
            bash -c "apt-get update && apt-get install -y dpkg-dev && cd /debs && dpkg-scanpackages . /dev/null | gzip -c > Packages.gz"

    done
    echo "✅ OS packages download and repository generation complete."
fi

echo "Copying custom stage to pi-gen..."
try_rm_rf "$STAGE_DST"
cp -R "$STAGE_SRC" "$STAGE_DST"

cd "$PI_GEN_DIR"
pwd

if [ "$REMOVE_DEPLOY" = true ]; then
    try_rm_rf_contents ./deploy
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
try_rm_rf "$STAGE_DST"

echo "Build complete."
exit 0
