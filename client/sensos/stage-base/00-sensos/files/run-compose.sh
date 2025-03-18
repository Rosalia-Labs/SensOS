#!/bin/bash
set -e

# Ensure SENSOS_USER is set
if [[ -z "$SENSOS_USER" ]]; then
    echo "ERROR: SENSOS_USER is not set in /etc/environment. Exiting."
    exit 1
fi

# Set home directory and paths
USER_HOME=$(eval echo ~$SENSOS_USER)
DOCKER_DIR="$USER_HOME/docker"
LOG_DIR="$USER_HOME/log"
LOGFILE="$LOG_DIR/launch.log"
SETTINGS_FILE="$USER_HOME/etc/network.conf"
COMPOSE_FILE_SOURCE="/usr/local/share/sensos/docker-compose.yml"

# Default values
DETACH="-d" # Default to detached mode
BUILD=""

# Parse command-line options
while [[ $# -gt 0 ]]; do
    case "$1" in
    --no-detach)
        DETACH="" # Remove '-d' option
        ;;
    --rebuild-containers)
        BUILD="--build" # Add '--build' option
        ;;
    *)
        echo "Unknown option: $1" | tee -a "$LOGFILE"
        exit 1
        ;;
    esac
    shift
done

# Ensure necessary directories exist
mkdir -p "$LOG_DIR"
mkdir -p "$DOCKER_DIR"

# Load settings file if available
if [[ -f "$SETTINGS_FILE" ]]; then
    source "$SETTINGS_FILE"
else
    echo "ERROR: Settings file not found at $SETTINGS_FILE" | tee -a "$LOGFILE"
    exit 1
fi

# Ensure SERVER_IP is set
if [[ -z "$SERVER_IP" ]]; then
    echo "ERROR: SERVER_IP variable is not set in $SETTINGS_FILE" | tee -a "$LOGFILE"
    exit 1
fi

# Copy docker-compose.yml to $DOCKER_DIR
if [[ -f "$COMPOSE_FILE_SOURCE" ]]; then
    cp "$COMPOSE_FILE_SOURCE" "$DOCKER_DIR/docker-compose.yml"
    echo "✅ Copied docker-compose.yml to $DOCKER_DIR" | tee -a "$LOGFILE"
else
    echo "❌ ERROR: Source docker-compose.yml not found at $COMPOSE_FILE_SOURCE" | tee -a "$LOGFILE"
    exit 1
fi

# Change directory to Docker directory
cd "$DOCKER_DIR"

cp COMPOSE_FILE_SOURCE ./docker-compose.yml

# Export variable for docker-compose
export SERVER_IP
export USER_HOME

# Start Docker Compose with the specified options
echo "🚀 Starting Docker Compose with server IP: $SERVER_IP" | tee -a "$LOGFILE"
docker compose up $BUILD $DETACH
