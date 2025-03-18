#!/bin/bash
set -e # Exit immediately on error

# Define variables
DOCKER_IMAGE="debian-sensos"
FILES_TAR="sensos-files.tar.gz"
LOCAL_FILES_DIR="../stage-base/00-sensos/files"

# Ensure the required files exist
if [ ! -d "$LOCAL_FILES_DIR" ]; then
    echo "❌ Error: Required directory not found: $LOCAL_FILES_DIR"
    exit 1
fi

# Create a tar archive of the files to include in the build
echo "📦 Creating tar archive of files..."
tar -czf "$FILES_TAR" -C "$LOCAL_FILES_DIR" .

# Build the Docker image, including the tar archive
echo "🔨 Building Docker image: $DOCKER_IMAGE..."
docker build --build-arg FILES_TAR="$FILES_TAR" -t "$DOCKER_IMAGE" .

# Remove the tar archive after the build
rm -f "$FILES_TAR"

# Run the container with privileges for WireGuard & Docker (without mounting the host's Docker socket)
echo "🚀 Starting container with WireGuard and Docker support..."
docker run --rm -it \
    --name sensos-test \
    --privileged \
    --cap-add=NET_ADMIN --cap-add=SYS_MODULE \
    --device /dev/net/tun \
    -v /lib/modules:/lib/modules:ro \
    "$DOCKER_IMAGE"
