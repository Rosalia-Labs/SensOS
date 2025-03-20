#!/bin/bash -e

# Only write to /etc/environment if FIRST_USER_NAME is set
if [[ -n "${FIRST_USER_NAME}" ]]; then
    echo "SENSOS_USER=${FIRST_USER_NAME}" >>"${ROOTFS_DIR}/etc/environment"
fi

BIN_DIR="${ROOTFS_DIR}/usr/local/bin"
SHARE_DIR="${ROOTFS_DIR}/usr/local/share/sensos"
SYSD_SYS_DIR="${ROOTFS_DIR}/etc/systemd/system"

SERVICES_DIR=files/services
SCRIPTS_DIR=files/scripts
DOCKER_DIR=files/docker
KEYS_DIR=files/keys

# Ensure /usr/local/share/sensos exists
mkdir -p "$SHARE_DIR"

# Install scripts to /usr/local/bin
install -m 755 "${SCRIPTS_DIR}/config-wifi-access-point" "${BIN_DIR}"
install -m 755 "${SCRIPTS_DIR}/config-sensos-containers" "${BIN_DIR}"
install -m 755 "${SCRIPTS_DIR}/list-registry-images" "${BIN_DIR}"
install -m 755 "${SCRIPTS_DIR}/push-registry-images" "${BIN_DIR}"
install -m 755 "${SCRIPTS_DIR}/config-sensos-client" "${BIN_DIR}"
install -m 755 "${SCRIPTS_DIR}/config-sensos-modem" "${BIN_DIR}"
install -m 755 "${SCRIPTS_DIR}/config-eeprom" "${BIN_DIR}"
install -m 755 "${SCRIPTS_DIR}/show-eeprom" "${BIN_DIR}"

# Install service files
install -m 644 "${SERVICES_DIR}/monitor-connectivity.service" "${SYSD_SYS_DIR}"
install -m 644 "${SERVICES_DIR}/wifi-access-point.service" "${SYSD_SYS_DIR}"
install -m 644 "${SERVICES_DIR}/set-sensos-user.service" "${SYSD_SYS_DIR}"
install -m 644 "${SERVICES_DIR}/sensos-modem.service" "${SYSD_SYS_DIR}"

# Install service start scripts
install -m 755 ${SERVICES_DIR}/start-monitor-connectivity.sh "${BIN_DIR}"
install -m 755 ${SERVICES_DIR}/start-wifi-access-point.sh "${BIN_DIR}"
install -m 755 ${SERVICES_DIR}/start-sensos-containers.sh "${BIN_DIR}"
install -m 755 ${SERVICES_DIR}/start-set-sensos-user.sh "${BIN_DIR}"
install -m 755 ${SERVICES_DIR}/start-sensos-modem.sh "${BIN_DIR}"

# Install docker image directories
cp -a "$DOCKER_DIR" "${SHARE_DIR}"

# Install init.d script for EEPROM configuration
install -m 755 "${SCRIPTS_DIR}/config-geekworm-ups-once" "${ROOTFS_DIR}/etc/init.d/"
install -m 755 "${SCRIPTS_DIR}/enable-wifi-access-point-first" "${ROOTFS_DIR}/etc/init.d/"

AUTHORIZED_KEYS="${KEYS_DIR}/sensos_admin_authorized_keys"
install -m 600 "$AUTHORIZED_KEYS" "${SHARE_DIR}/sensos_admin_authorized_keys"
