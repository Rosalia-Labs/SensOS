#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

load_images_from_disk() {
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
            echo "[INFO] Loading image '$image_name' from $tarball..."
            if [[ "$tarball" == *.gz ]]; then
                if gunzip -c "$tarball" | docker load; then
                    echo "[INFO] Load succeeded. Deleting $tarball"
                    rm -f "$tarball"
                else
                    echo "[ERROR] Failed to load image from $tarball" >&2
                    exit 1
                fi
            else
                if docker load <"$tarball"; then
                    echo "[INFO] Load succeeded. Deleting $tarball"
                    rm -f "$tarball"
                else
                    echo "[ERROR] Failed to load image from $tarball" >&2
                    exit 1
                fi
            fi
        else
            echo "[INFO] No tarball found for image '$image_name'. Will check build."
        fi
    done < <(find "$base_dir" -type f -name 'Dockerfile' -exec dirname {} \;)
}

build_missing_images() {
    local base_dir="${1:-/sensos/docker}"
    local bakefile="$(mktemp /tmp/docker-bake.XXXXXX.hcl)"
    echo "[INFO] Generating bake file $bakefile..."

    echo 'group "default" {' >"$bakefile"
    echo '  targets = [' >>"$bakefile"

    while IFS= read -r docker_dir; do
        image_name="sensos-client-$(basename "$docker_dir" | tr '_' '-')"

        if ! docker image inspect "$image_name" >/dev/null 2>&1; then
            echo "  \"$image_name\"," >>"$bakefile"
        fi
    done < <(find "$base_dir" -type f -name 'Dockerfile' -exec dirname {} \;)

    echo '  ]' >>"$bakefile"
    echo '}' >>"$bakefile"

    while IFS= read -r docker_dir; do
        image_name="sensos-client-$(basename "$docker_dir" | tr '_' '-')"

        if ! docker image inspect "$image_name" >/dev/null 2>&1; then
            echo "target \"$image_name\" {" >>"$bakefile"
            echo "  context = \"$docker_dir\"" >>"$bakefile"
            echo "  tags = [\"$image_name\"]" >>"$bakefile"
            echo "}" >>"$bakefile"
        fi
    done < <(find "$base_dir" -type f -name 'Dockerfile' -exec dirname {} \;)

    if grep -q 'targets = \[\]' "$bakefile"; then
        echo "[INFO] No missing images to build."
        rm -f "$bakefile"
        return 0
    fi

    echo "[INFO] Running docker compose bake..."
    docker buildx bake --file "$bakefile" --load
    rm -f "$bakefile"
}
