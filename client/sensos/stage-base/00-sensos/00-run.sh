#!/bin/bash -e

# Only write to /etc/environment if FIRST_USER_NAME is set
if [[ -n "${FIRST_USER_NAME}" ]]; then
    echo "SENSOS_USER=${FIRST_USER_NAME}" >>"${ROOTFS_DIR}/etc/environment"
fi

BIN_DIR="${ROOTFS_DIR}/usr/local/bin"
SHARE_DIR="${ROOTFS_DIR}/usr/local/share/sensos"

# Ensure /usr/local/share/sensos exists
mkdir -p "$SHARE_DIR"

# Install scripts to /usr/local/bin
install -m 755 files/config-sensos-client "${BIN_DIR}"
install -m 755 files/config-eeprom "${BIN_DIR}"
install -m 755 files/config-modem "${BIN_DIR}"
install -m 755 files/monitor-connectivity.sh "${BIN_DIR}"
install -m 755 files/config-access-point "${BIN_DIR}"
install -m 755 files/start-access-point "${BIN_DIR}"
install -m 755 files/start-modem "${BIN_DIR}"
install -m 755 files/set-sensos-user.sh "${BIN_DIR}"

# Install docker-compose.yml to /usr/local/share/sensos
install -m 644 files/docker-compose.yml "${SHARE_DIR}/docker-compose.yml"

# Install init.d script for EEPROM configuration
install -m 755 files/config-eeprom-once "${ROOTFS_DIR}/etc/init.d/"

SYSD_SYS_DIR="${ROOTFS_DIR}/etc/systemd/system"

# Install correct service files
install -m 644 files/monitor-connectivity.service "${SYSD_SYS_DIR}/monitor-connectivity.service"
install -m 644 files/wifi-access-point.service "${SYSD_SYS_DIR}/wifi-access-point.service"
install -m 644 files/set-sensos-user.service "${SYSD_SYS_DIR}/set-sensos-user.service"
install -m 644 files/sensos-modem.service "${SYSD_SYS_DIR}/sensos-modem.service"
