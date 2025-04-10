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

# Register options
register_option "--offline" "OFFLINE_MODE" "Force offline mode (disables pulls)" "false"
register_option "--build" "NEEDS_BUILD" "Build images before starting" "false"
register_option "--detach" "DETACH_MODE" "Run containers in the background" "false"
register_option "--compose-file" "COMPOSE_FILE" "Path to docker-compose file" "docker-compose.yml"

# Parse CLI args
parse_switches "$0" "$@"

# Apply config-based connectivity rule if not overridden by --offline
: "${connectivity:=unrestricted}"
case "$connectivity" in
none | restricted)
    OFFLINE=true
    ;;
unrestricted)
    OFFLINE=false
    ;;
*)
    echo "Warning: Unknown connectivity level: $connectivity"
    OFFLINE=true
    ;;
esac

# CLI flag overrides config
if [[ "$OFFLINE_MODE" == true ]]; then
    OFFLINE=true
fi

# Load Docker images from tarballs
load_missing_images_from_disk() {
    local base_dir="/sensos/docker"
    echo "[INFO] Searching for Docker image tarballs under $base_dir..."

    while IFS= read -r docker_dir; do
        image_name="sensos-$(basename "$docker_dir" | tr '_' '-')"

        tarball=""
        if [[ -f "$docker_dir/${image_name}.tar.gz" ]]; then
            tarball="$docker_dir/${image_name}.tar.gz"
        elif [[ -f "$docker_dir/${image_name}.tar" ]]; then
            tarball="$docker_dir/${image_name}.tar"
        fi

        if [[ -n "$tarball" ]]; then
            echo "[INFO] Force loading image '$image_name' from $tarball..."
            if [[ "$tarball" == *.gz ]]; then
                if gunzip -c "$tarball" | docker load; then
                    echo "[INFO] Load succeeded. Deleting $tarball"
                    rm -f "$tarball"
                else
                    echo "[ERROR] Failed to load image from $tarball"
                fi
            else
                if docker load <"$tarball"; then
                    echo "[INFO] Load succeeded. Deleting $tarball"
                    rm -f "$tarball"
                else
                    echo "[ERROR] Failed to load image from $tarball"
                fi
            fi
        else
            echo "[INFO] No tarball found for image '$image_name' in $docker_dir"
        fi
    done < <(find "$base_dir" -type f -name 'Dockerfile' -exec dirname {} \;)
}

echo "[INFO] Preloading images from disk..."
load_missing_images_from_disk

# Prepare Docker Compose command
COMPOSE_CMD=(docker compose -f "$COMPOSE_FILE")

if [[ "$OFFLINE" == true ]]; then
    echo "[INFO] Offline mode: disabling remote pulls"
    export DOCKER_BUILDKIT=1
    export COMPOSE_DOCKER_CLI_BUILD=1
    COMPOSE_CMD+=("--pull" "never")
fi

if [[ "$NEEDS_BUILD" == true ]]; then
    COMPOSE_CMD+=("--build")
fi

echo "[INFO] Starting containers..."
if [[ "$DETACH_MODE" == true ]]; then
    "${COMPOSE_CMD[@]}" up -d
else
    "${COMPOSE_CMD[@]}" up
fi
