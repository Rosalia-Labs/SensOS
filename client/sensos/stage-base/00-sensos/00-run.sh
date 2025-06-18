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

on_chroot <<'EOF'
for script in "/sensos/scripts/"* "/sensos/service_scripts/"*; do
    chmod +x "$script"
    ln -sf "$script" "/usr/local/bin/$(basename "$script")"
done

for svc in /sensos/services/*.service; do
    ln -sf "$svc" "/etc/systemd/system/$(basename "$svc")"
done
EOF

if [ -d "$SENSOS_DIR/init.d" ]; then
    echo "Ensuring scripts in /sensos/init.d are executable"
    find "$SENSOS_DIR/init.d" -type f -exec chmod +x {} +
fi

echo "sensos overlay setup complete"
