#!/bin/bash

on_chroot <<'EOF'
set -e
export DEBIAN_FRONTEND=noninteractive

# Create group and admin user
addgroup sensos-data
adduser --disabled-password --gecos "SensOS Admin" --shell /bin/bash sensos-admin

# Make sure that the docker group exists
getent group docker || groupadd --system docker

# Add user to needed groups
for grp in sudo sensos-data dialout plugdev netdev docker audio gpio i2c spi; do
    adduser sensos-admin "$grp"
done

# Sudo with no password
echo "sensos-admin ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/sensos-admin
chmod 0440 /etc/sudoers.d/sensos-admin

# Set permissions on /sensos for shared group access
chown -R sensos-admin:sensos-data /sensos
chmod -R 2775 /sensos

# Lock password (key-based login only)
passwd -l sensos-admin

# SSH key setup
mkdir -p /home/sensos-admin/.ssh
if [ -f /sensos/keys/sensos_admin_authorized_keys ]; then
    mv -f /sensos/keys/sensos_admin_authorized_keys /home/sensos-admin/.ssh/authorized_keys
    chmod 0600 /home/sensos-admin/.ssh/authorized_keys
fi
chown -R sensos-admin:sensos-admin /home/sensos-admin/.ssh
chmod 0700 /home/sensos-admin/.ssh
EOF
