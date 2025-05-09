#!/bin/bash -e

SYSD_SYS_DIR="${ROOTFS_DIR}/etc/systemd/system"
SENSOS_DIR="${ROOTFS_DIR}/sensos"
BIN_DIR="${ROOTFS_DIR}/usr/local/bin"
FILES_DIR="files"

# Ensure base dirs exist
mkdir -p "$SENSOS_DIR/log" "$SENSOS_DIR/data/audio_recordings" "$SENSOS_DIR/data/database"

# Enable nullglob so globs return empty instead of literal pattern
shopt -s nullglob

if [ -d "${FILES_DIR}/keys" ]; then
    find "${FILES_DIR}/keys" -type f -exec chmod 600 {} +
    find "${FILES_DIR}/keys" -type d -exec chmod 700 {} +
fi

# Install to /usr/local/bin
for f in "${FILES_DIR}/scripts/"* "${FILES_DIR}/service_scripts/"*; do
    [[ -f "$f" ]] && install -m 755 "$f" "$BIN_DIR"
done

# Install systemd services
for f in "${FILES_DIR}/services/"*; do
    [[ -f "$f" ]] && install -m 644 "$f" "$SYSD_SYS_DIR"
done

# Copy everything else to /sensos/<name>
for subdir in "${FILES_DIR}/"*/; do
    name=$(basename "$subdir")
    case "$name" in
    scripts | service_scripts | services) ;;
    *)
        echo "Copying ${name} → /sensos/${name}"
        mkdir -p "$SENSOS_DIR/$name"
        files=("$subdir"/*)
        if [ ${#files[@]} -gt 0 ]; then
            cp -a "${files[@]}" "$SENSOS_DIR/$name/"
        fi
        if [ "$name" = "init.d" ]; then
            echo "Ensuring scripts in /sensos/init.d are executable"
            find "$SENSOS_DIR/$name" -type f -exec chmod +x {} +
        fi
        ;;
    esac
done

shopt -u nullglob
