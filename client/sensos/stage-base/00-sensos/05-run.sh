on_chroot <<EOF
[ -f /sensos/keys/api_password ] && \
  install -m 0600 -o sensos-admin -g sensos-data "/sensos/keys/api_password" "/sensos/.sensos_api_password"

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

grep -q 'i2c-dev' /etc/modules || echo 'i2c-dev' >> /etc/modules

grep -q '^dtoverlay=w1-gpio' /boot/firmware/config.txt || echo 'dtoverlay=w1-gpio' >> /boot/firmware/config.txt
grep -q 'w1-gpio' /etc/modules || echo 'w1-gpio' >> /etc/modules
grep -q 'w1-therm' /etc/modules || echo 'w1-therm' >> /etc/modules

grep -q '^dtparam=spi=on' /boot/firmware/config.txt || echo 'dtparam=spi=on' >> /boot/firmware/config.txt
grep -q 'spi-dev' /etc/modules || echo 'spi-dev' >> /etc/modules
EOF
