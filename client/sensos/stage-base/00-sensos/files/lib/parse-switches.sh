#!/bin/bash
set -e

script_name=$(basename "$0")

source /sensos/lib/load-defaults.sh
source /sensos/lib/parse-switches.sh
source /sensos/lib/docker-utils.sh

# Load defaults (optional here but consistent)
load_defaults /sensos/etc/defaults.conf "$script_name"

# Register CLI options
register_option --offline OFFLINE_MODE "Disable network access (build without pulling images)" "false"

# Parse CLI args
parse_switches "$script_name" "$@"

cd /sensos/docker

echo "[INFO] Loading any available images from local tarballs..."
load_images_from_disk

# Check for missing images
missing_images=()
while IFS= read -r docker_dir; do
    image_name="sensos-client-$(basename "$docker_dir" | tr '_' '-')"
    if ! docker image inspect "$image_name" >/dev/null 2>&1; then
        missing_images+=("$image_name")
    fi
done < <(find . -type f -name 'Dockerfile' -exec dirname {} \;)

# Decide based on missing images
if [[ "${#missing_images[@]}" -gt 0 ]]; then
    echo "[INFO] Missing Docker images detected:"
    printf '  %s\n' "${missing_images[@]}"

    if [[ "$OFFLINE_MODE" == true ]]; then
        echo "[FATAL] Cannot build missing images: offline mode enabled (--offline, equivalent to --pull=never)." >&2
        exit 1
    else
        echo "[INFO] Building missing images (network access allowed)..."
        build_missing_images
        echo "[INFO] Image build complete."
    fi
else
    echo "[INFO] All required Docker images are already available locally. No build needed."
fi
