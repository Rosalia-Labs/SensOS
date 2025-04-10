#!/bin/bash
set -e

# Load defaults
if [[ -f /sensos/lib/load-defaults.sh ]]; then
    source /sensos/lib/load-defaults.sh
    load_defaults /sensos/etc/defaults.conf "$(basename "$0")"
else
    echo "Error: /sensos/lib/load-defaults.sh not found." >&2
    exit 1
fi

# Load CLI parser
if [[ -f /sensos/lib/parse-switches.sh ]]; then
    source /sensos/lib/parse-switches.sh
else
    echo "Error: /sensos/lib/parse-switches.sh not found." >&2
    exit 1
fi

# Register supported option
register_option "--compose-file" "COMPOSE_FILE" "Path to docker-compose file" "docker-compose.yml"

# Parse command line
parse_switches "$0" "$@"

echo "[INFO] Stopping containers using $COMPOSE_FILE..."
docker compose -f "$COMPOSE_FILE" down
