#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -e

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd $WORK_DIR

# List all containers with names starting with "sensos-"
containers=$(docker ps --filter "name=^sensos-" --format '{{.Names}}')

if [ -z "$containers" ]; then
    echo "❌ No running sensos containers found. Exiting."
    exit 1
fi

BACKUP_DIR="./backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p "$BACKUP_DIR"

for container in $containers; do
    echo "💾 Processing container: $container"

    # Ensure container is running (should be, because we filtered running ones)
    if ! docker ps --filter "name=^${container}$" --format '{{.Names}}' | grep -q "^${container}$"; then
        echo "❌ Container $container is not running. Exiting."
        exit 1
    fi

    # Check if /etc/wireguard exists in the container
    if ! docker exec "$container" sh -c 'test -d /etc/wireguard'; then
        echo "ℹ️  Container $container does not have an /etc/wireguard directory. Skipping backup for this container."
        continue
    fi

    # Check if there are any *.conf files in /etc/wireguard
    if ! docker exec "$container" sh -c 'ls /etc/wireguard/*.conf 1>/dev/null 2>&1'; then
        echo "ℹ️  No *.conf files found in /etc/wireguard in $container. Skipping backup for this container."
        continue
    fi

    # Prepare backup file name (strip "sensos-" prefix)
    container_name="${container#sensos-}"
    WG_BACKUP_FILE="$BACKUP_DIR/wg_${container_name}_${TIMESTAMP}.tgz"

    echo "💾 Backing up WireGuard configs from $container..."
    # Change directory into /etc/wireguard so the tarball doesn't include the full path
    docker exec "$container" sh -c "cd /etc/wireguard && tar czf - *.conf" >"$WG_BACKUP_FILE"

    if [ $? -eq 0 ] && [ -s "$WG_BACKUP_FILE" ]; then
        echo "✅ WireGuard backup completed for $container: $WG_BACKUP_FILE"
        chmod 600 "$WG_BACKUP_FILE"
    else
        echo "❌ Failed to backup WireGuard configs from $container. Exiting."
        rm -f "$WG_BACKUP_FILE"
        exit 1
    fi
done

echo "✅ All WireGuard backups completed successfully."
