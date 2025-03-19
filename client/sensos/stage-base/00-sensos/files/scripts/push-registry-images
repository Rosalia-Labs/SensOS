#!/bin/bash
# registry-update.sh
# This script builds Docker images from subdirectories and pushes them to the sensos registry
# if they are updated. It accepts command-line options to override default registry connection parameters.
# This version uses HTTP (insecure) and supports the switches:
# --registry-ip, --registry-port, --registry-user, and --registry-password.

set -e # Exit on error

# Default values
SENSOS_REGISTRY_IP="localhost"
SENSOS_REGISTRY_PORT=5000
SENSOS_REGISTRY_USER="sensos"
SENSOS_REGISTRY_PASSWORD="sensos"

usage() {
    echo "Usage: $0 [--registry-ip IP] [--registry-port PORT] [--registry-user USER] [--registry-password PASS]"
    exit 1
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
    --registry-ip)
        SENSOS_REGISTRY_IP="$2"
        shift 2
        ;;
    --registry-port)
        SENSOS_REGISTRY_PORT="$2"
        shift 2
        ;;
    --registry-user)
        SENSOS_REGISTRY_USER="$2"
        shift 2
        ;;
    --registry-password)
        SENSOS_REGISTRY_PASSWORD="$2"
        shift 2
        ;;
    --help)
        usage
        ;;
    *)
        echo "Unknown option: $1"
        usage
        ;;
    esac
done

echo "Using registry IP: $SENSOS_REGISTRY_IP"

# Build the registry address (without protocol prefix)
DOCKER_REGISTRY="$SENSOS_REGISTRY_IP:$SENSOS_REGISTRY_PORT"

# Log in to the Docker registry using HTTP (insecure)
echo "$SENSOS_REGISTRY_PASSWORD" | docker login "http://$DOCKER_REGISTRY" -u "$SENSOS_REGISTRY_USER" --password-stdin

# Loop through each subdirectory (each representing an image) and build/push images as needed
for dir in */; do
    dir=${dir%/} # Remove trailing slash
    image_name="$DOCKER_REGISTRY/$dir:latest"

    echo "Checking if $image_name needs an update..."

    # Build the image locally using the directory as build context
    docker build -t "$image_name" "$dir"

    # Get the local image digest from RepoDigests (set after a push)
    local_digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$image_name" 2>/dev/null | awk -F'@' '{print $2}')

    # Get the remote image digest from the registry via response headers
    remote_digest=$(curl -sI -u "$SENSOS_REGISTRY_USER:$SENSOS_REGISTRY_PASSWORD" \
        "http://$DOCKER_REGISTRY/v2/$dir/manifests/latest" | grep -i "^Docker-Content-Digest:" | awk '{print $2}' | tr -d '\r')

    if [ -z "$remote_digest" ]; then
        echo "No remote digest found; assuming image has not been pushed previously."
    fi

    echo "Local digest:  $local_digest"
    echo "Remote digest: $remote_digest"

    # If the local and remote digests match (and local_digest is non-empty), skip pushing
    if [[ "$local_digest" == "$remote_digest" && -n "$local_digest" ]]; then
        echo "✅ $image_name is up to date. Skipping push."
    else
        echo "🚀 Updating $image_name..."
        docker push "$image_name"
    fi
done

echo "All necessary images have been updated."
