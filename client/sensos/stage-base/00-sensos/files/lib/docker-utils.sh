#!/bin/bash

load_missing_images_from_disk() {
    local base_dir="${1:-/sensos/docker}"
    echo "[INFO] Searching for Docker image tarballs under $base_dir..."

    while IFS= read -r docker_dir; do
        image_name="sensos-client-$(basename "$docker_dir" | tr '_' '-')"

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
