#!/bin/bash

on_chroot <<'EOF'
set -e

# Create system user with no home dir by default, add home if needed
adduser --system --shell /bin/bash --gecos "Sensos Runner" sensos-runner

# Add user to all required groups
for grp in sudo sensos-data dialout plugdev netdev docker audio gpio i2c spi; do
    adduser sensos-runner "$grp"
done

# Allow passwordless sudo for automation
echo "sensos-runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/sensos-runner
chmod 0440 /etc/sudoers.d/sensos-runner

# Lock password (so only key/sudo/su can be used)
passwd -l sensos-runner
EOF

echo "Completed SensOS 03-run.sh"
