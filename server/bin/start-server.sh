#!/bin/bash
set -e

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$WORK_DIR"

echo "Working directory: $(pwd)"

# Default options
REBUILD=false
DETACH=true
RESTART=false

# Load environment variables from .env if available
if [ -f ".env" ]; then
    set -a
    source ".env"
    set +a
else
    echo "❌ .env file not found at $WORK_DIR/.env. Exiting."
    exit 1
fi

# Load versioning information from VERSION if available
VERSION_FILE="$WORK_DIR/../VERSION"
if [ -f "$VERSION_FILE" ]; then
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

export VERSION_MAJOR VERSION_MINOR VERSION_PATCH VERSION_SUFFIX
export GIT_COMMIT GIT_BRANCH GIT_TAG GIT_DIRTY

# Parse command-line arguments
while [ $# -gt 0 ]; do
    case "$1" in
    --rebuild-containers)
        REBUILD=true
        ;;
    --no-detach)
        DETACH=false
        ;;
    --restart)
        RESTART=true
        ;;
    --help)
        echo "Usage: $0 [--rebuild-containers] [--no-detach] [--restart]"
        exit 0
        ;;
    *)
        echo "Unknown option: $1" >&2
        exit 1
        ;;
    esac
    shift
done

# Check if any required containers are already running
if docker ps --filter "name=sensos-" --format '{{.Names}}' | grep -q .; then
    if [ "$RESTART" = true ]; then
        echo "ℹ️  Restart option enabled. Stopping running SensOS containers..."
        "$WORK_DIR/bin/stop-server.sh"

        echo "⏳ Waiting for containers to stop..."
        TIMEOUT=60 # Max wait time in seconds
        ELAPSED=0
        while docker ps --filter "name=sensos-" --format '{{.Names}}' | grep -q .; do
            if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
                echo "❌ Timeout reached while waiting for containers to stop."
                exit 1
            fi
            sleep 5
            ((ELAPSED += 5))
        done
    else
        echo "❌ One or more SensOS containers are already running. Exiting script."
        echo "ℹ️  Use ./stop-server.sh to shut the server down before restarting, or run with --restart."
        exit 1
    fi
fi

# Construct the docker compose command using an array for the final startup.
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
