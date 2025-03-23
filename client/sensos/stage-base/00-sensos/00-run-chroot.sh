#!/bin/bash -e

# Enable i2c
raspi-config nonint do_i2c 0

# Install latest docker
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
        docker-compose-plugin \
        qemu-user-static

# Create sensos-admin account for ssh login
USERNAME="sensos-admin"
USER_HOME="/home/$USERNAME"

if ! id "$USERNAME" &>/dev/null; then
    useradd -m -s /bin/bash -c "Sensos Admin" "$USERNAME"
    echo "Created user ${USERNAME}."
fi

install -m 440 /dev/null "/etc/sudoers.d/$USERNAME"
echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" >"/etc/sudoers.d/$USERNAME"

mkdir -p "${USER_HOME}/.ssh"
mv "/sensos/sensos_admin_authorized_keys" "${USER_HOME}/.ssh/authorized_keys"
chmod 600 "${USER_HOME}/.ssh/authorized_keys"
chown -R "${USERNAME}:${USERNAME}" "${USER_HOME}/.ssh"

passwd -l "$USERNAME"

mkdir -p /sensos
chown -R "${USERNAME}:${USERNAME}" /sensos
chmod -R 2775 /sensos

USERNAME="sensos-runner"

if ! id "$USERNAME" &>/dev/null; then
    useradd --system -c "Sensos Runner" "$USERNAME"
    echo "Created user ${USERNAME}."
fi

install -m 440 /dev/null "/etc/sudoers.d/$USERNAME"
echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" >"/etc/sudoers.d/$USERNAME"

adduser "${USERNAME}" sensos-admin
adduser "${USERNAME}" dialout || true
adduser "${USERNAME}" plugdev || true
adduser "${USERNAME}" netdev || true
adduser "${USERNAME}" audio || true
adduser "${USERNAME}" gpio || true
adduser "${USERNAME}" i2c || true
adduser "${USERNAME}" spi || true

passwd -l "$USERNAME"

# Let first user run docker
if [ -n "${FIRST_USER_NAME}" ]; then
    adduser "${FIRST_USER_NAME}" docker
    adduser "${FIRST_USER_NAME}" sensos-admin
fi

install -d -m 2775 -o sensos-admin -g sensos-admin /sensos/etc
install -d -m 2775 -o sensos-admin -g sensos-admin /sensos/log
install -d -m 2775 -o sensos-admin -g sensos-admin /sensos/data
