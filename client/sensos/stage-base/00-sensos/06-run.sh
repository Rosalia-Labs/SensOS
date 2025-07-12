#!/bin/bash

# Setup chrony as the NTP client and disable systemd-timesyncd
on_chroot <<EOF
ln -sf /sensos/etc/chrony.conf /etc/chrony/chrony.conf
systemctl enable chrony
systemctl disable systemd-timesyncd || true
EOF

# Enable status updates
on_chroot <<EOF
systemctl enable send-status-update.timer
EOF

# Enable monitoring disk usage
on_chroot <<EOF
systemctl enable monitor-disk-space.timer
EOF

echo "Completed SensOS 06-run.sh"
