#!/bin/sh

set -e

# Default options
REMOVE_VOLUMES=false
NO_REBUILD=false
SAVE_DATABASE=true

# Load environment variables from .env if available
if [ -f .env ]; then
    echo "📄 Loading environment variables from .env..."
    set -a
    . .env
    set +a
else
    echo "❌ .env file not found. Run configure-server.sh. Exiting." >&2
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

    GIT_COMMIT=$(awk -F' = ' '/^commit/ {print $2}' "$VERSION_FILE")
    GIT_BRANCH=$(awk -F' = ' '/^branch/ {print $2}' "$VERSION_FILE")
    GIT_TAG=$(awk -F' = ' '/^tag/ {print $2}' "$VERSION_FILE")
    GIT_DIRTY=$(awk -F' = ' '/^dirty/ {print $2}' "$VERSION_FILE")
else
    echo "⚠️ VERSION file not found. Proceeding without version overrides."
fi

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
    --remove-volumes)
        REMOVE_VOLUMES=true
        ;;
    --no-build)
        NO_REBUILD=true
        ;;
    --no-save-database)
        SAVE_DATABASE=false
        ;;
    --help)
        echo "Usage: $0 [--remove-volumes] [--no-build] [--no-save-database]"
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

# Stop Docker Compose services
echo "🛑 Stopping Docker Compose services..."
if [ "$REMOVE_VOLUMES" = true ]; then
    docker-compose down -v
else
    docker-compose down
fi

# Start Docker Compose services with or without rebuild
if [ "$NO_REBUILD" = false ]; then
    echo "🚀 Starting Docker Compose services with build..."
    env | grep 'VERSION\|GIT_' # Debugging step to confirm variables are exported
    docker-compose up -d --build
else
    echo "🚀 Starting Docker Compose services without rebuild..."
    env | grep 'VERSION\|GIT_' # Debugging step
    docker-compose up -d
fi

echo "✅ Done."

# Print version information for verification
echo "📌 Running software version:"
echo "   Version: ${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}-${VERSION_SUFFIX}"
echo "   Git: commit=${GIT_COMMIT}, branch=${GIT_BRANCH}, tag=${GIT_TAG}, dirty=${GIT_DIRTY}"
