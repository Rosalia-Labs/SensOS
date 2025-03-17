#!/bin/bash
set -e

# Default options
REBUILD=false
DETACH=true

# Load environment variables from .env if available
if [ -f "$(dirname "$0")/.env" ]; then
    echo "📄 Loading environment variables from .env..."
    set -a
    source "$(dirname "$0")/.env"
    set +a
else
    echo "❌ .env file not found at $(dirname "$0")/.env. Exiting."
    exit 1
fi

# Load versioning information from ../VERSION if available
VERSION_FILE="$(dirname "$0")/../VERSION"
if [ -f "$VERSION_FILE" ]; then
    echo "📄 Loading versioning information from $VERSION_FILE..."
    VERSION_MAJOR=$(awk -F' = ' '/^major/ {print $2}' "$VERSION_FILE")
    VERSION_MINOR=$(awk -F' = ' '/^minor/ {print $2}' "$VERSION_FILE")
    VERSION_PATCH=$(awk -F' = ' '/^patch/ {print $2}' "$VERSION_FILE")
    VERSION_SUFFIX=$(awk -F' = ' '/^suffix/ {print $2}' "$VERSION_FILE")
else
    echo "⚠️ VERSION file not found. Proceeding without version overrides."
fi

# Get Git metadata dynamically
GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
GIT_TAG=$(git describe --tags --always 2>/dev/null || echo "unknown")
GIT_DIRTY=$(test -n "$(git status --porcelain 2>/dev/null)" && echo "true" || echo "false")

# Ensure empty values are set to defaults
VERSION_MAJOR="${VERSION_MAJOR:-unknown}"
VERSION_MINOR="${VERSION_MINOR:-unknown}"
VERSION_PATCH="${VERSION_PATCH:-unknown}"
VERSION_SUFFIX="${VERSION_SUFFIX:-}"
GIT_COMMIT="${GIT_COMMIT:-unknown}"
GIT_BRANCH="${GIT_BRANCH:-unknown}"
GIT_TAG="${GIT_TAG:-unknown}"
GIT_DIRTY="${GIT_DIRTY:-false}"

# Parse command-line arguments
while [ $# -gt 0 ]; do
    case "$1" in
    --rebuild-containers)
        REBUILD=true
        ;;
    --no-detach)
        DETACH=false
        ;;
    --help)
        echo "Usage: $0 [--rebuild-containers] [--no-detach]"
        exit 0
        ;;
    *)
        echo "Unknown option: $1" >&2
        exit 1
        ;;
    esac
    shift
done

export VERSION_MAJOR VERSION_MINOR VERSION_PATCH VERSION_SUFFIX
export GIT_COMMIT GIT_BRANCH GIT_TAG GIT_DIRTY

echo "📦 Pre-pulling required images..."
docker pull debian:bookworm-slim
docker pull postgres:17-bookworm
docker pull lscr.io/linuxserver/wireguard:latest
docker pull registry:2

# Construct the docker compose command using an array
if [ "$REBUILD" = true ]; then
    if [ "$DETACH" = true ]; then
        CMD=(docker compose up -d --build)
    else
        CMD=(docker compose up --build)
    fi
else
    if [ "$DETACH" = true ]; then
        CMD=(docker compose up -d)
    else
        CMD=(docker compose up)
    fi
fi

echo "🚀 Executing command: ${CMD[*]}"

echo "✅ Done."

# Print version information for verification
echo "📌 Running software version:"
echo "   Version: ${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}-${VERSION_SUFFIX}"
echo "   Git: commit=${GIT_COMMIT}, branch=${GIT_BRANCH}, tag=${GIT_TAG}, dirty=${GIT_DIRTY}"

# Execute the constructed command as the last line
exec "${CMD[@]}"
