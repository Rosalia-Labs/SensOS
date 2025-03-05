#!/bin/bash

# Load environment variables
ENV_FILE="../../../server/.env"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
else
    echo "Error: Environment file $ENV_FILE not found!"
    exit 1
fi

# Define registry URL (use HTTPS)
REGISTRY="https://$SENSOS_REGISTRY_IP:$SENSOS_REGISTRY_PORT"

# Get list of repositories (images)
REPOS=$(curl -s -u "$SENSOS_REGISTRY_USER:$SENSOS_REGISTRY_PASSWORD" --insecure "$REGISTRY/v2/_catalog" | grep -o '"repositories":\[[^]]*' | cut -d '[' -f2 | tr -d '"]' | tr ',' '\n')

# Check if there are any images
if [ -z "$REPOS" ]; then
    echo "No repositories found or registry unreachable."
    exit 1
fi

# List images and their tags
for repo in $REPOS; do
    echo "Image: $repo"

    # Get tags for the image
    TAGS=$(curl -s -u "$SENSOS_REGISTRY_USER:$SENSOS_REGISTRY_PASSWORD" --insecure "$REGISTRY/v2/$repo/tags/list" | grep -o '"tags":\[[^]]*' | cut -d '[' -f2 | tr -d '"]' | tr ',' '\n')

    if [ -z "$TAGS" ]; then
        echo "  (No tags found)"
    else
        for tag in $TAGS; do
            echo "  - $tag"
        done
    fi
done
