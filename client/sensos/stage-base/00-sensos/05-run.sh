on_chroot <<EOF
# Remove keys dir as we're done with it
rm -rf /sensos/keys || true

# Persistent logs
install -d -m 2755 -o root -g systemd-journal /var/log/journal

# Ensure i2c module loads at boot
grep -q 'i2c_bcm2835' /etc/modules || \
  echo 'i2c_bcm2835' >> /etc/modules

# Enable i2c
grep -q '^dtparam=i2c_arm=on' /boot/firmware/config.txt || \
  echo 'dtparam=i2c_arm=on' >> /boot/firmware/config.txt
EOF
