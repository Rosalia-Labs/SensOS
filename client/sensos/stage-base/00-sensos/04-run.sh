#!/bin/bash -e
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

[ -f /config ] && source /config

if [ "${ENABLE_HOTSPOT}" = "1" ]; then
    on_chroot <<EOF
systemctl enable auto-hotspot.service
EOF
    echo "Enabled auto-hotspot service."
else
    echo "Skipped enabling of auto-hotspot service."
fi

echo "Completed SensOS 04-run.sh"
