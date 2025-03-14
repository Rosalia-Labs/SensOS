#!/bin/bash -e

# Copy the registry certificate into the trusted CA directory
install -m 644 files/domain.crt "${ROOTFS_DIR}/usr/local/share/ca-certificates/domain.crt"

# Install the sensos client configuration script
install -m 755 files/config-sensos-client "${ROOTFS_DIR}/usr/local/bin"

install -m 755 files/monitor-connectivity.sh "${ROOTFS_DIR}/usr/local/bin"
install -m 644 files/monitor-connectivity.service "${ROOTFS_DIR}/etc/systemd/system/monitor-connectivity.service"
