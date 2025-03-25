#!/bin/bash -e

SYSD_SYS_DIR="${ROOTFS_DIR}/etc/systemd/system"
SENSOS_DIR="${ROOTFS_DIR}/sensos"
BIN_DIR="${ROOTFS_DIR}/usr/local/bin"

SERVICE_SCRIPTS_DIR=files/service_scripts
SERVICES_DIR=files/services
SCRIPTS_DIR=files/scripts
DOCKER_DIR=files/docker
LIB_DIR=files/lib
ETC_DIR=files/etc

# Ensure /sensos subdirectories exist
mkdir -p "$SENSOS_DIR/log" "$SENSOS_DIR/etc" "$SENSOS_DIR/data" "$SENSOS_DIR/lib"

# Enable nullglob to avoid errors on empty directories
shopt -s nullglob

# Install scripts to /usr/local/bin
for script in "${SCRIPTS_DIR}"/*; do
    install -m 755 "$script" "$BIN_DIR"
done

# Install service files
for service in "${SERVICES_DIR}"/*; do
    install -m 644 "$service" "$SYSD_SYS_DIR"
done

# Install service start scripts
for script in "${SERVICE_SCRIPTS_DIR}"/*; do
    install -m 755 "$script" "$BIN_DIR"
done

shopt -u nullglob

# Copy lib and etc directories
cp -a "$LIB_DIR/." "$SENSOS_DIR/lib/"
cp -a "$ETC_DIR/." "$SENSOS_DIR/etc/"

# Install docker image directories
cp -a "$DOCKER_DIR" "$SENSOS_DIR"
