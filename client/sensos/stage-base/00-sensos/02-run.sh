#!/bin/bash

KEYS_DIR=files/keys
AUTHORIZED_KEYS="${KEYS_DIR}/sensos_admin_authorized_keys"

SENSOS_DIR="${ROOTFS_DIR}/sensos"

install -m 600 "$AUTHORIZED_KEYS" "${SENSOS_DIR}/sensos_admin_authorized_keys"

on_chroot <<EOF
useradd -m -s /bin/bash -c "Sensos SSH account" sensos-admin
useradd sensos-admin sudo
chown -R sensos-admin:sensos-admin /sensos
chmod -R 2775 /sensos
if [ -f /sensos/sensos_admin_authorized_keys ]; then
    mkdir -p /home/sensos-admin/.ssh
    mv /sensos/sensos_admin_authorized_keys /home/sensos-admin/.ssh/authorized_keys
    chown -R sensos-admin:sensos-admin /home/sensos-admin/.ssh
    chmod 0600 /home/sensos-admin/.ssh/authorized_keys
fi
passwd -l sensos-admin
EOF
