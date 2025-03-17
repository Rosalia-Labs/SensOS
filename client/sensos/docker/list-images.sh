#!/bin/bash
# registry-list.sh
# This script lists repositories and tags from the sensos registry.
# It accepts command-line options to override default connection parameters.

# Default values
SENSOS_REGISTRY_IP="auto"
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

# If the registry IP is set to "auto", determine it from the sensos-api-proxy container
if [ "$SENSOS_REGISTRY_IP" = "auto" ]; then
    SENSOS_REGISTRY_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' sensos-api-proxy)
    if [ -z "$SENSOS_REGISTRY_IP" ]; then
        echo "Failed to determine sensos-api-proxy IP address. Is the container running?"
        exit 1
    fi
fi

echo "Using registry IP: $SENSOS_REGISTRY_IP"

# Define the registry URL (using HTTPS)
REGISTRY="https://$SENSOS_REGISTRY_IP:$SENSOS_REGISTRY_PORT"

# Get list of repositories (images)
REPOS=$(curl -s -u "$SENSOS_REGISTRY_USER:$SENSOS_REGISTRY_PASSWORD" --insecure "$REGISTRY/v2/_catalog" |
    grep -o '"repositories":\[[^]]*' | cut -d '[' -f2 | tr -d '"]' | tr ',' '\n')

# Check if there are any images
if [ -z "$REPOS" ]; then
    echo "No repositories found or registry unreachable."
    exit 1
fi

# List images and their tags
for repo in $REPOS; do
    echo "Image: $repo"
    TAGS=$(curl -s -u "$SENSOS_REGISTRY_USER:$SENSOS_REGISTRY_PASSWORD" --insecure "$REGISTRY/v2/$repo/tags/list" |
        grep -o '"tags":\[[^]]*' | cut -d '[' -f2 | tr -d '"]' | tr ',' '\n')
    if [ -z "$TAGS" ]; then
        echo "  (No tags found)"
    else
        for tag in $TAGS; do
            echo "  - $tag"
        done
    fi
done
