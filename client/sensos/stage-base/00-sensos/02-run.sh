#!/bin/bash

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
mkdir -p /home/sensos-admin/.ssh
mv -f /sensos/keys/sensos_admin_authorized_keys /home/sensos-admin/.ssh/authorized_keys || true
chown -R sensos-admin:sensos-admin /home/sensos-admin/.ssh
chmod 0600 /home/sensos-admin/.ssh/authorized_keys || true
chmod 0700 /home/sensos-admin/.ssh
EOF
