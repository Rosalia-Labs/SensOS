#!/bin/sh

set -e

# Default options
REMOVE_VOLUMES=false
SAVE_DATABASE=true

# Suppress docker compose warnings
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
    --no-save-database)
        SAVE_DATABASE=false
        ;;
    --help)
        echo "Usage: $0 [--remove-volumes] [--no-save-database]"
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

# Check if sensos-database is running
DB_RUNNING=$(docker ps -q -f name=sensos-database)

# If --remove-volumes is given and --no-save-database is NOT given, ensure database is backed up
if [ "$REMOVE_VOLUMES" = true ] && [ "$SAVE_DATABASE" = true ]; then
    if [ -n "$DB_RUNNING" ]; then
        # Database is running → Backup before removing volumes
        BACKUP_DIR="./backups"
        TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
        BACKUP_FILE="$BACKUP_DIR/db_backup_${TIMESTAMP}.sql.gz"

        mkdir -p "$BACKUP_DIR"

        echo "💾 Backing up database to $BACKUP_FILE before removing volumes..."
        docker exec -t sensos-database pg_dumpall -U postgres | gzip >"$BACKUP_FILE"

        if [ $? -eq 0 ]; then
            echo "✅ Database backup completed successfully: $BACKUP_FILE"
        else
            echo "❌ Database backup failed. Not removing volumes." >&2
            REMOVE_VOLUMES=false
        fi
    else
        # Database is not running → Do not remove volumes
        echo "⚠️ sensos-database is not running. Skipping database backup and preserving volumes."
        REMOVE_VOLUMES=false
    fi
fi

# Stop Docker Compose services
echo "🛑 Stopping Docker Compose services..."
if [ "$REMOVE_VOLUMES" = true ]; then
    docker-compose down -v
else
    docker-compose down
fi

echo "✅ Done."
