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

# Create user for ssh admin sessions
USERNAME="sensos-admin"
AUTHORIZED_KEYS="files/keys/sensos_admin_authorized_keys"

# Create the user without a password and disable console login
if ! id "$USERNAME" &>/dev/null; then
    useradd -m -s /bin/bash -G sudo,adm,netdev,docker -c "Sensos Admin" "$USERNAME"
fi

# Grant sudo privileges
echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" >/etc/sudoers.d/$USERNAME
chmod 0440 /etc/sudoers.d/$USERNAME

# Copy the authorized_keys file from a predefined location
if [[ -f "$AUTHORIZED_KEYS" ]]; then
    mkdir -p /home/$USERNAME/.ssh
    chmod 700 /home/$USERNAME/.ssh
    chown $USERNAME:$USERNAME /home/$USERNAME/.ssh
    cp "$AUTHORIZED_KEYS" /home/$USERNAME/.ssh/authorized_keys
    chmod 600 /home/$USERNAME/.ssh/authorized_keys
    chown $USERNAME:$USERNAME /home/$USERNAME/.ssh/authorized_keys
else
    echo "WARNING: No authorized_keys file found at $AUTHORIZED_KEYS"
fi

# Ensure user cannot log in with a password
passwd -l "$USERNAME"
