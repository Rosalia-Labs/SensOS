#!/bin/bash

AUTHORIZED_KEYS_SRC="files/keys/sensos_admin_authorized_keys"
AUTHORIZED_KEYS_DST="${ROOTFS_DIR}/sensos/sensos_admin_authorized_keys"

if [ -f "$AUTHORIZED_KEYS_SRC" ]; then
    echo "Copying admin authorized keys file."
    mkdir -p ${ROOTFS_DIR}/sensos
    cp $AUTHORIZED_KEYS_SRC $AUTHORIZED_KEYS_DST
else
    echo "No admin authorized keys found."
fi

on_chroot <<EOF
addgroup sensos-data
adduser --disabled-password --gecos "SensOS Admin" --shell /bin/bash sensos-admin
adduser sensos-admin sudo
adduser sensos-admin sensos-data
adduser sensos-admin dialout
adduser sensos-admin plugdev
adduser sensos-admin netdev
adduser sensos-admin docker
adduser sensos-admin audio
adduser sensos-admin gpio
adduser sensos-admin i2c
adduser sensos-admin spi
echo "sensos-admin ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/sensos-admin
chmod 0440 /etc/sudoers.d/sensos-admin
chown -R sensos-admin:sensos-data /sensos
chmod -R 2775 /sensos
passwd -l sensos-admin
EOF

if [ -f "$AUTHORIZED_KEYS_DST" ]; then
    on_chroot <<EOF
mkdir -p /home/sensos-admin/.ssh
mv -f /sensos/sensos_admin_authorized_keys /home/sensos-admin/.ssh/authorized_keys
chown -R sensos-admin:sensos-admin /home/sensos-admin/.ssh
chmod 0600 /home/sensos-admin/.ssh/authorized_keys
EOF
fi
