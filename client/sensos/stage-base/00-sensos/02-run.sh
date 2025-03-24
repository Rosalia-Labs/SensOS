#!/bin/bash

KEYS_DIR=files/keys
AUTHORIZED_KEYS="${KEYS_DIR}/sensos_admin_authorized_keys"

SENSOS_DIR="${ROOTFS_DIR}/sensos"

install -m 600 "$AUTHORIZED_KEYS" "${SENSOS_DIR}/sensos_admin_authorized_keys" || true

on_chroot <<EOF
addgroup sensos-data || true
adduser --disabled-password --shell /bin/bash sensos-admin || true
adduser sensos-admin sudo || true
adduser sensos-admin sensos-data || true
adduser sensos-admin dialout || true
adduser sensos-admin plugdev || true
adduser sensos-admin netdev || true
adduser sensos-admin docker || true
adduser sensos-admin audio || true
adduser sensos-admin gpio || true
adduser sensos-admin i2c || true
adduser sensos-admin spi || true
mkdir -p /home/sensos-admin/.ssh
mv /sensos/sensos_admin_authorized_keys /home/sensos-admin/.ssh/authorized_keys || true
chown -R sensos-admin:sensos-admin /home/sensos-admin/.ssh
chmod 0600 /home/sensos-admin/.ssh/authorized_keys || true
echo "sensos-admin ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/sensos-admin
chmod 0440 /etc/sudoers.d/sensos-admin
chown -R sensos-admin:sensos-data /sensos
chmod -R 2775 /sensos
passwd -l sensos-admin
EOF
