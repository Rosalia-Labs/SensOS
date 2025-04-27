#!/bin/bash
set -e

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../docker/" && pwd)"
cd "$WORK_DIR"
echo "Working directory: $(pwd)"

# Default options
REMOVE_VOLUMES=false
BACKUP=false
NO_BACKUP=false

# Suppress docker compose warnings by setting version variables (if needed downstream)
VERSION_MAJOR=0
VERSION_MINOR=0
VERSION_PATCH=0
VERSION_SUFFIX=0
GIT_COMMIT=0
GIT_BRANCH=0
GIT_TAG=0
GIT_DIRTY=0

# Parse command-line arguments
while [ $# -gt 0 ]; do
    case "$1" in
    --remove-volumes)
        REMOVE_VOLUMES=true
        ;;
    --backup)
        BACKUP=true
        ;;
    --no-backup)
        NO_BACKUP=true
        ;;
    --help)
        echo "Usage: $0 [--remove-volumes] [--backup] [--no-backup]"
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

# Determine if backup should be performed
PERFORM_BACKUP=false
if [ "$NO_BACKUP" = false ] && { [ "$BACKUP" = true ] || [ "$REMOVE_VOLUMES" = true ]; }; then
    PERFORM_BACKUP=true
fi

if [ "$PERFORM_BACKUP" = true ]; then
    echo "💾 Initiating backup process..."
    # Execute the backup scripts.
    "$WORK_DIR"/../bin/backup-database.sh
    db_status=$?

    "$WORK_DIR"/../bin/backup-wireguard.sh
    wg_status=$?

    # If either backup fails, disable volume removal.
    if [ $db_status -ne 0 ] || [ $wg_status -ne 0 ]; then
        echo "❌ One or more backup processes failed. Not removing volumes."
        REMOVE_VOLUMES=false
    fi
fi

# Stop Docker Compose services
echo "🛑 Stopping Docker Compose services..."
if [ "$REMOVE_VOLUMES" = true ]; then
    docker compose down -v
else
    docker compose down
fi

echo "✅ Done."
