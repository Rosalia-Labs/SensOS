#!/bin/bash

on_chroot <<EOF
adduser --system -c "Sensos Runner" sensos-runner
adduser sensos-runner sudo || true
adduser sensos-runner sensos-data || true
adduser sensos-runner dialout || true
adduser sensos-runner plugdev || true
adduser sensos-runner netdev || true
adduser sensos-runner docker || true
adduser sensos-runner audio || true
adduser sensos-runner gpio || true
adduser sensos-runner i2c || true
adduser sensos-runner spi || true
echo "sensos-runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/sensos-runner
chmod 0440 /etc/sudoers.d/sensos-runner
passwd -l sensos-runner
EOF
