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
    if ! groups "${FIRST_USER_NAME}" | grep -q "\bdocker\b"; then
        adduser "${FIRST_USER_NAME}" docker
    fi
fi

USERNAME="sensos-admin"
USER_HOME="/home/$USERNAME"

if ! id "$USERNAME" &>/dev/null; then
    useradd -m -s /bin/bash -G sudo -c "Sensos Admin" "$USERNAME"
    echo "Created user ${USERNAME}."
fi

install -m 440 /dev/null "/etc/sudoers.d/$USERNAME"
echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" >"/etc/sudoers.d/$USERNAME"

SHARE_DIR=/usr/local/share/sensos

mkdir -p "${USER_HOME}/.ssh"
mv "${SHARE_DIR}/sensos_admin_authorized_keys" "${USER_HOME}/.ssh/authorized_keys"
chmod 600 "${USER_HOME}/.ssh/authorized_keys"
chown -R "${USERNAME}:${USERNAME}" "${USER_HOME}/.ssh"

mv -f ${SHARE_DIR}/docker ${USER_HOME}
chown -R "${USERNAME}:${USERNAME}" "${USER_HOME}/docker"

passwd -l "$USERNAME"

if [ -n "${FIRST_USER_NAME}" ]; then
    chown -R "${FIRST_USER_NAME}:${FIRST_USER_NAME}" "${SHARE_DIR}"
fi

dphys-swapfile swapoff
systemctl disable dphys-swapfile
dphys-swapfile uninstall
