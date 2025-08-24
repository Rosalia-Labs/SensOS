# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

on_chroot <<EOF
set -e
export DEBIAN_FRONTEND=noninteractive

echo "Installing prerequisites for Docker..."
apt-get update &&
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl

echo "Setting up Docker GPG key..."
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo "Adding Docker repository..."
echo "deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian \$(. /etc/os-release && echo \"\$VERSION_CODENAME\") stable" \
    > /etc/apt/sources.list.d/docker.list

echo "Installing Docker components..."
apt-get update &&
    apt-get install -y --no-install-recommends \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin \
        qemu-user-static

echo "Docker installation complete."
EOF

echo "Completed SensOS 01-run.sh"
