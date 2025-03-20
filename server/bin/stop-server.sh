#!/bin/bash

set -e

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd $WORK_DIR

echo "$(pwd)"

# Default options
REMOVE_VOLUMES=false
BACKUP=false
NO_BACKUP=false

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

# Backup function
backup_data() {
    BACKUP_DIR="./backups"
    TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
    mkdir -p "$BACKUP_DIR"

    # Backup database
    DB_RUNNING=$(docker ps -q -f name=sensos-database)
    if [ -n "$DB_RUNNING" ]; then
        BACKUP_FILE="$BACKUP_DIR/db_backup_${TIMESTAMP}.sql.gz"
        echo "💾 Backing up database to $BACKUP_FILE..."
        docker exec -t sensos-database pg_dumpall -U postgres | gzip >"$BACKUP_FILE"
        if [ $? -eq 0 ]; then
            echo "✅ Database backup completed successfully: $BACKUP_FILE"
        else
            echo "❌ Database backup failed. Not removing volumes." >&2
            REMOVE_VOLUMES=false
        fi
    else
        echo "⚠️ sensos-database is not running. Skipping database backup."
    fi

    # Backup WireGuard
    CONTROLLER_RUNNING=$(docker ps -q -f name=sensos-controller)
    if [ -n "$CONTROLLER_RUNNING" ]; then
        BACKUP_FILE="$BACKUP_DIR/wg_backup_${TIMESTAMP}.tgz"
        echo "💾 Backing up WireGuard configs..."
        docker exec -t sensos-controller tar czf - /config /etc/wireguard >"$BACKUP_FILE"
        if [ $? -eq 0 ]; then
            echo "✅ WireGuard backup completed successfully: $BACKUP_FILE"
            chmod 600 "$BACKUP_FILE"
        else
            echo "❌ WireGuard backup failed. Not removing volumes." >&2
            REMOVE_VOLUMES=false
        fi
    else
        echo "⚠️ sensos-controller is not running. Skipping WireGuard backup."
    fi

    # If either service is not running, prevent volume removal
    if [ -z "$DB_RUNNING" ] || [ -z "$CONTROLLER_RUNNING" ]; then
        if [ "$REMOVE_VOLUMES" = true ]; then
            echo "⚠️ Required services are not running. Not removing volumes."
            REMOVE_VOLUMES=false
        fi
    fi
}

# Perform backup if required
if [ "$PERFORM_BACKUP" = true ]; then
    backup_data
fi

# Stop Docker Compose services
echo "🛑 Stopping Docker Compose services..."
if [ "$REMOVE_VOLUMES" = true ]; then
    docker compose down -v
else
    docker compose down
fi

echo "✅ Done."
