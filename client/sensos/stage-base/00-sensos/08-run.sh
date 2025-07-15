#!/bin/bash -e
#
# 08-run.sh: Configure persistent systemd-journald logs with resource limits.
# To be run by pi-gen in the build stage (inside chroot).

on_chroot <<'EOF'
set -e

mkdir -p /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal || true
chmod 2755 /var/log/journal
chown root:systemd-journal /var/log/journal

JOURNALD_CONF="/etc/systemd/journald.conf"

if [ ! -f "$JOURNALD_CONF" ]; then
    touch "$JOURNALD_CONF"
fi

sed -i 's|^#*Storage=.*|Storage=persistent|' "$JOURNALD_CONF"
grep -q "^Storage=" "$JOURNALD_CONF" || echo "Storage=persistent" >> "$JOURNALD_CONF"

sed -i 's|^#*SystemMaxUse=.*|SystemMaxUse=128M|' "$JOURNALD_CONF"
grep -q "^SystemMaxUse=" "$JOURNALD_CONF" || echo "SystemMaxUse=128M" >> "$JOURNALD_CONF"

sed -i 's|^#*SystemMaxFileSize=.*|SystemMaxFileSize=32M|' "$JOURNALD_CONF"
grep -q "^SystemMaxFileSize=" "$JOURNALD_CONF" || echo "SystemMaxFileSize=32M" >> "$JOURNALD_CONF"

sed -i 's|^#*SystemMaxFiles=.*|SystemMaxFiles=4|' "$JOURNALD_CONF"
grep -q "^SystemMaxFiles=" "$JOURNALD_CONF" || echo "SystemMaxFiles=4" >> "$JOURNALD_CONF"

sed -i 's|^#*MaxRetentionSec=.*|MaxRetentionSec=2week|' "$JOURNALD_CONF"
grep -q "^MaxRetentionSec=" "$JOURNALD_CONF" || echo "MaxRetentionSec=2week" >> "$JOURNALD_CONF"

EOF
