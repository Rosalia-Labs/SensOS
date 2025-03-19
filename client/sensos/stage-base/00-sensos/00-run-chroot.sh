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

USERNAME="sensos-admin"
TARGET_HOME="/home/$USERNAME"

if ! id "$USERNAME" &>/dev/null; then
    useradd -m -s /bin/bash -G sudo -c "Sensos Admin" "$USERNAME"
fi

install -m 440 /dev/null "/etc/sudoers.d/$USERNAME"
echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" >"/etc/sudoers.d/$USERNAME"

if [[ -d "$TARGET_HOME" ]]; then
    OWNER=$(stat -c "%U" "$TARGET_HOME")
    if [[ "$OWNER" != "$USERNAME" ]]; then
        echo "⚠️ Home directory $TARGET_HOME is owned by $OWNER. Fixing ownership..."
        chown -R "$USERNAME:$USERNAME" "$TARGET_HOME"
    fi
fi

chown -R "$USERNAME:$USERNAME" "$TARGET_HOME/.ssh"

passwd -l "$USERNAME"
