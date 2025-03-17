#!/bin/bash
# registry-update.sh
# This script builds Docker images from subdirectories and pushes them to the sensos registry
# if they are updated. It accepts command-line options to override default registry connection parameters.

set -e # Exit on error

# Default values (can be overridden via command-line switches)
SENSOS_REGISTRY_PORT="${SENSOS_REGISTRY_PORT:-5000}"
SENSOS_REGISTRY_USER="${SENSOS_REGISTRY_USER:-sensos}"
SENSOS_REGISTRY_PASSWORD="${SENSOS_REGISTRY_PASSWORD:-sensos}"
DEFAULT_REGISTRY_DNS="registry.sensos.internal"

usage() {
    echo "Usage: $0 [--registry-port PORT] [--registry-user USER] [--registry-password PASS] [--registry-dns DNS]"
    exit 1
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
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
    --registry-dns)
        REGISTRY_DNS="$2"
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

# Use the fixed DNS name (default or provided)
REGISTRY_DNS=${REGISTRY_DNS:-$DEFAULT_REGISTRY_DNS}
DOCKER_REGISTRY="$REGISTRY_DNS:$SENSOS_REGISTRY_PORT"
echo "Using registry DNS: $REGISTRY_DNS"

# Log in to the Docker registry using HTTPS
echo "$SENSOS_REGISTRY_PASSWORD" | docker login "https://$DOCKER_REGISTRY" -u "$SENSOS_REGISTRY_USER" --password-stdin

# Loop through each subdirectory (each representing an image) and build/push images as needed
for dir in */; do
    dir=${dir%/} # Remove trailing slash
    image_name="$DOCKER_REGISTRY/$dir:latest"

    echo "Checking if $image_name needs an update..."

    # Build the image locally using the directory as build context
    docker build -t "$image_name" "$dir"

    # Get the local image digest from RepoDigests (set after a push)
    local_digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$image_name" 2>/dev/null | awk -F '@' '{print $2}')

    # Get the remote image digest from the registry via response headers
    remote_digest=$(curl -sI -u "$SENSOS_REGISTRY_USER:$SENSOS_REGISTRY_PASSWORD" --insecure \
        -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
        "https://$DOCKER_REGISTRY/v2/$dir/manifests/latest" | grep -i "^Docker-Content-Digest:" | awk '{print $2}' | tr -d '\r')

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
