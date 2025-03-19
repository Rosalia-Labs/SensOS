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

# Ensure /usr/local/share/sensos exists
mkdir -p "$SHARE_DIR"

# Install scripts to /usr/local/bin
install -m 755 "${SCRIPTS_DIR}/list-registry-images" "${BIN_DIR}"
install -m 755 "${SCRIPTS_DIR}/push-registry-images" "${BIN_DIR}"
install -m 755 "${SCRIPTS_DIR}/config-sensos-client" "${BIN_DIR}"
install -m 755 "${SCRIPTS_DIR}/config-eeprom" "${BIN_DIR}"
install -m 755 "${SCRIPTS_DIR}/show-eeprom" "${BIN_DIR}"

# Install service files
install -m 644 "${SERVICES_DIR}/monitor-connectivity.service" "${SYSD_SYS_DIR}"
install -m 644 "${SERVICES_DIR}/wifi-access-point.service" "${SYSD_SYS_DIR}"
install -m 644 "${SERVICES_DIR}/set-sensos-user.service" "${SYSD_SYS_DIR}"
install -m 644 "${SERVICES_DIR}/sensos-modem.service" "${SYSD_SYS_DIR}"

# Install config scripts to /usr/local/bin
install -m 755 ${SERVICES_DIR}/config-sensos-modem "${BIN_DIR}"
install -m 755 ${SERVICES_DIR}/config-wifi-access-point "${BIN_DIR}"
install -m 755 ${SERVICES_DIR}/config-sensos-containers "${BIN_DIR}"

# Install service start scripts
install -m 755 ${SERVICES_DIR}/start-monitor-connectivity.sh "${BIN_DIR}"
install -m 755 ${SERVICES_DIR}/start-sensos-containers.sh "${BIN_DIR}"
install -m 755 ${SERVICES_DIR}/start-set-sensos-user.sh "${BIN_DIR}"
install -m 755 ${SERVICES_DIR}/start-wifi-access-point "${BIN_DIR}"
install -m 755 ${SERVICES_DIR}/start-sensos-modem "${BIN_DIR}"

# Install docker-compose.yml to /usr/local/share/sensos
install -m 644 "$DOCKER_DIR/docker-compose.yml" "${SHARE_DIR}/docker-compose.yml"

# Install docker image directories
cp -r "$DOCKER_DIR/sound_capture" "${SHARE_DIR}"
cp -r "$DOCKER_DIR/database" "${SHARE_DIR}"
cp -r "$DOCKER_DIR/birdnet" "${SHARE_DIR}"

# Install init.d script for EEPROM configuration
install -m 755 "${SCRIPTS_DIR}/config-eeprom-once" "${ROOTFS_DIR}/etc/init.d/"
