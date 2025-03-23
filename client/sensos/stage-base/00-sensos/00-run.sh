#!/bin/bash -e

BIN_DIR="${ROOTFS_DIR}/usr/local/bin"
SYSD_SYS_DIR="${ROOTFS_DIR}/etc/systemd/system"
SENSOS_DIR="${ROOTFS_DIR}/sensos"

SERVICE_SCRIPTS_DIR=files/service_scripts
SERVICES_DIR=files/services
SCRIPTS_DIR=files/scripts
DOCKER_DIR=files/docker
KEYS_DIR=files/keys

# Ensure /usr/local/share/sensos exists
mkdir -p "$SENSOS_DIR"

# Install scripts to /usr/local/bin
for script in ${SCRIPTS_DIR}/*; do
    install -m 755 "${script}" "${BIN_DIR}"
done

# Install service files
for service in ${SERVICES_DIR}/*; do
    install -m 644 "${service}" "${SYSD_SYS_DIR}"
done

# Install service start scripts
for script in ${SERVICE_SCRIPTS_DIR}/*; do
    install -m 755 "${script}" "${BIN_DIR}"
done

# Install docker image directories
cp -a "$DOCKER_DIR" "${SENSOS_DIR}"

AUTHORIZED_KEYS="${KEYS_DIR}/sensos_admin_authorized_keys"
install -m 600 "$AUTHORIZED_KEYS" "${SENSOS_DIR}/sensos_admin_authorized_keys"
