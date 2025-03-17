#!/bin/bash -e

# Install the sensos client configuration script
install -m 755 files/config-sensos-client "${ROOTFS_DIR}/usr/local/bin"
install -m 755 files/config-eeprom "${ROOTFS_DIR}/usr/local/bin"
install -m 755 files/config-modem "${ROOTFS_DIR}/usr/local/bin"
install -m 755 files/monitor-connectivity.sh "${ROOTFS_DIR}/usr/local/bin"
install -m 644 files/monitor-connectivity.service "${ROOTFS_DIR}/etc/systemd/system/monitor-connectivity.service"

install -m 755 files/config-eeprom-once "${ROOTFS_DIR}/etc/init.d/"
