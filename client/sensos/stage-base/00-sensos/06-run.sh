on_chroot <<EOF
# Remove keys
rm -rf /sensos/keys || true

# Persistent logs
install -d -m 2755 -o root -g systemd-journal /var/log/journal

# Ensure i2c module loads at boot
grep -q 'i2c_bcm2835' /etc/modules || \
  echo 'i2c_bcm2835' >> /etc/modules

# Modem settings -- should be optional
grep -q '^dtparam=i2c_arm_baudrate=' /boot/firmware/config.txt || \
  echo 'dtparam=i2c_arm_baudrate=10000' >> /boot/firmware/config.txt
EOF
