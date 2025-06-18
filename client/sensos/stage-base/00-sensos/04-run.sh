#!/bin/bash -e

[ -f /config ] && source /config

if [ "${ENABLE_HOTSPOT}" = "1" ]; then
    on_chroot <<EOF
systemctl enable auto-hotspot.service
EOF
    echo "Enabled auto-hotspot service."
else
    echo "Skipped enabling of auto-hotspot service."
fi

if [ "${ENABLE_GEEKWORM_EEPROM}" = "1" ]; then
    on_chroot <<EOF
systemctl enable config-geekworm-eeprom.service
EOF
    echo "Enabled config-geekworm-eeprom service."
else
    echo "Skipped enabling of config-geekworm-eeprom service."
fi
