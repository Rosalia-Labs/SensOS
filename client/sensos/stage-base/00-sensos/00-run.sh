#!/bin/bash -e

SYSD_SYS_DIR="${ROOTFS_DIR}/etc/systemd/system"
SENSOS_DIR="${ROOTFS_DIR}/sensos"
BIN_DIR="${ROOTFS_DIR}/usr/local/bin"
FILES_DIR="files"

if [ -d "${FILES_DIR}/keys" ]; then
    find "${FILES_DIR}/keys" -type f -exec chmod 600 {} +
    find "${FILES_DIR}/keys" -type d -exec chmod 700 {} +
fi

mkdir -p "$SENSOS_DIR"
(cd "$FILES_DIR" && tar --exclude-from=../tar-excludes -cf - .) | tar -xf - -C "$SENSOS_DIR"

for script in "$SENSOS_DIR/scripts/"* "$SENSOS_DIR/service_scripts/"*; do
    [ -f "$script" ] && ln -sf "$script" "$BIN_DIR/"
done

for svc in "$SENSOS_DIR/services/"*; do
    [ -f "$svc" ] && ln -sf "$svc" "$SYSD_SYS_DIR/"
done

if [ -d "$SENSOS_DIR/init.d" ]; then
    echo "Ensuring scripts in /sensos/init.d are executable"
    find "$SENSOS_DIR/init.d" -type f -exec chmod +x {} +
fi

echo "sensos overlay setup complete"
