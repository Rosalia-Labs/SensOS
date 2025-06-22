#!/bin/bash
# Hardware enablement: Ensure I2C, 1-wire, and SPI are configured for the Pi

on_chroot <<EOF
# I2C setup
grep -q 'i2c_bcm2835' /etc/modules || echo 'i2c_bcm2835' >> /etc/modules
grep -q '^dtparam=i2c_arm=on' /boot/firmware/config.txt || echo 'dtparam=i2c_arm=on' >> /boot/firmware/config.txt
grep -q 'i2c-dev' /etc/modules || echo 'i2c-dev' >> /etc/modules

# 1-wire setup
grep -q '^dtoverlay=w1-gpio' /boot/firmware/config.txt || echo 'dtoverlay=w1-gpio' >> /boot/firmware/config.txt
grep -q 'w1-gpio' /etc/modules || echo 'w1-gpio' >> /etc/modules
grep -q 'w1-therm' /etc/modules || echo 'w1-therm' >> /etc/modules

# SPI setup
grep -q '^dtparam=spi=on' /boot/firmware/config.txt || echo 'dtparam=spi=on' >> /boot/firmware/config.txt
grep -q 'spi-dev' /etc/modules || echo 'spi-dev' >> /etc/modules
EOF

echo "Completed SensOS 05-run.sh"
