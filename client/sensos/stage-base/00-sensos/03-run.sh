#!/bin/bash

on_chroot <<EOF
useradd --system -c "Sensos Runner" sensos-runner
useradd sensos-runner sudo
useradd sensos-runner dialout
useradd sensos-runner plugdev
useradd sensos-runner netdev
useradd sensos-runner audio
useradd sensos-runner gpio
useradd sensos-runner i2c
useradd sensos-runner spi
passwd -l sensos-runner
EOF
