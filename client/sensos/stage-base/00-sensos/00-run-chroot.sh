#!/bin/bash -e

# Enable i2c
raspi-config nonint do_i2c 0

apt-get update &&
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl

# Create directory for Docker's GPG key and add it
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

# Add Docker repository
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    >/etc/apt/sources.list.d/docker.list

# Update package lists
apt-get update &&
    apt-get install -y --no-install-recommends \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin

if [ -n "${FIRST_USER_NAME}" ]; then
    adduser "${FIRST_USER_NAME}" docker
fi
