#!/bin/bash

on_chroot <<EOF
adduser --system -c "Sensos Runner" sensos-runner
adduser sensos-runner sudo 
adduser sensos-runner sensos-data 
adduser sensos-runner dialout
adduser sensos-runner plugdev 
adduser sensos-runner netdev 
adduser sensos-runner docker
adduser sensos-runner audio 
adduser sensos-runner gpio
adduser sensos-runner i2c 
adduser sensos-runner spi
echo "sensos-runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/sensos-runner
chmod 0440 /etc/sudoers.d/sensos-runner
passwd -l sensos-runner
EOF
