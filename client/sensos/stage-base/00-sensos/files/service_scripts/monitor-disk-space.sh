#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

source /sensos/lib/parse-switches.sh

register_option --simulate-min-mb simulate_min_mb "Simulate minimum available MB for test runs" ""

parse_switches "$0" "$@"

# List of mountpoints to monitor
MOUNTPOINTS=("/" "/sensos/data")

# Services and thresholds (service_name stop_MB start_MB)
SERVICES=(
    "sensos-arecord   500   1000"
    "read-i2c-sensors 500   1000"
    "sensos-container 100   800"
)

# Find the minimum available MB across all mountpoints
if [[ -n "$simulate_min_mb" ]]; then
    MIN_MB="$simulate_min_mb"
    TEST_MODE=1
else
    MIN_MB=""
    for MP in "${MOUNTPOINTS[@]}"; do
        if df -P "$MP" &>/dev/null; then
            MB=$(df -P "$MP" | awk 'NR==2 {print int($4/1024)}')
            if [ -z "$MIN_MB" ] || [ "$MB" -lt "$MIN_MB" ]; then
                MIN_MB=$MB
            fi
        else
            logger -t diskmon "Warning: mountpoint $MP not found"
        fi
    done
fi

# If no mountpoints were available, abort
if [ -z "$MIN_MB" ]; then
    logger -t diskmon "No valid mountpoints found, aborting"
    exit 1
fi

# Now, control each service based on the minimum available MB
for entry in "${SERVICES[@]}"; do
    read -r SERVICE STOP_MB START_MB <<<"$entry"
    IS_ACTIVE=$(systemctl is-active "$SERVICE" 2>/dev/null)

    if [ "$MIN_MB" -lt "$STOP_MB" ] && [ "$IS_ACTIVE" = "active" ]; then
        if [[ -n "$TEST_MODE" ]]; then
            echo "[SIMULATE] Would stop $SERVICE: minimum space $MIN_MB MB < $STOP_MB MB"
        else
            systemctl stop "$SERVICE"
            logger -t diskmon "Stopped $SERVICE: minimum space $MIN_MB MB < $STOP_MB MB"
        fi
    fi

    if [ "$MIN_MB" -gt "$START_MB" ] && [ "$IS_ACTIVE" != "active" ]; then
        if [[ -n "$TEST_MODE" ]]; then
            echo "[SIMULATE] Would start $SERVICE: minimum space $MIN_MB MB > $START_MB MB"
        else
            systemctl start "$SERVICE"
            logger -t diskmon "Started $SERVICE: minimum space $MIN_MB MB > $START_MB MB"
        fi
    fi
done
