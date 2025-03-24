#!/bin/bash -e

if [ "${ENABLE_FIRSTBOOT_HOTSPOT}" = "1" ]; then
    on_chroot <<'EOF'
systemctl enable auto-hotspot.service
systemctl start auto-hotspot.service || true
EOF
    echo "Enabled auto-hotspot service."
fi

if [ "${ENABLE_FIRSTBOOT_GEEKWORM_EEPROM}" = "1" ]; then
    on_chroot <<'EOF'
systemctl enable config-geekworm-eeprom.service
EOF
    echo "Enabled config-geekworm-eeprom service."
fi
