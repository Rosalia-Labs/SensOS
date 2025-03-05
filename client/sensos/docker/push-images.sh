#!/bin/bash

set -e # Exit on error

# Load authentication details
ENV_FILE="../../../server/.env"

if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
else
    echo "Error: Environment file $ENV_FILE not found!"
    exit 1
fi

# Ensure required environment variables exist
if [[ -z "$SENSOS_REGISTRY_IP" || -z "$SENSOS_REGISTRY_PORT" || -z "$SENSOS_REGISTRY_USER" || -z "$SENSOS_REGISTRY_PASSWORD" ]]; then
    echo "Error: Missing required environment variables for the registry"
    exit 1
fi

# Fix: Use only the registry address (no https://)
DOCKER_REGISTRY="$SENSOS_REGISTRY_IP:$SENSOS_REGISTRY_PORT"

# Log in to Docker registry
echo "$SENSOS_REGISTRY_PASSWORD" | docker login "https://$DOCKER_REGISTRY" -u "$SENSOS_REGISTRY_USER" --password-stdin

# Loop through each subdirectory and build/push images
for dir in */; do
    dir=${dir%/} # Remove trailing slash
    image_name="$DOCKER_REGISTRY/$dir:latest"

    echo "Checking if $image_name needs an update..."

    # Build image locally (now with correct tag format)
    docker build -t "$image_name" "$dir"

    # Get local image digest
    local_digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$image_name" | awk -F '@' '{print $2}')

    # Get remote image digest from the registry
    remote_digest=$(curl -s -u "$SENSOS_REGISTRY_USER:$SENSOS_REGISTRY_PASSWORD" --insecure \
        -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
        "https://$DOCKER_REGISTRY/v2/$dir/manifests/latest" | grep -o '"docker-content-digest":"[^"]*' | cut -d '"' -f4)

    # Compare digests
    if [[ "$local_digest" == "$remote_digest" ]]; then
        echo "✅ $image_name is up to date. Skipping push."
    else
        echo "🚀 Updating $image_name..."
        docker push "$image_name"
    fi
done

echo "All necessary images have been updated."
