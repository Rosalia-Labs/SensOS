#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

on_chroot <<EOF
ln -sf /sensos/etc/chrony.conf /etc/chrony/chrony.conf
systemctl enable chrony
systemctl disable systemd-timesyncd || true
EOF

on_chroot <<EOF
systemctl enable send-status-update.timer
EOF

on_chroot <<EOF
systemctl enable monitor-disk-space.timer
EOF

on_chroot <<'EOF'
systemctl enable cache-sys-info.service
EOF

on_chroot <<'EOF'
systemctl enable netstat.service
EOF

echo "Completed SensOS 06-run.sh"
