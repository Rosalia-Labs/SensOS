#!/bin/bash -e

on_chroot <<EOF
raspi-config nonint do_i2c 0
EOF

# Let first user run docker
if [ -n "${FIRST_USER_NAME}" ]; then
    on_chroot <<EOF
adduser "${FIRST_USER_NAME}" docker
adduser "${FIRST_USER_NAME}" sensos-admin
EOF
fi
