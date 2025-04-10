on_chroot <<EOF
install -m 644 /sensos/etc/chrony.conf /etc/chrony/chrony.conf
systemctl enable chrony
systemctl disable systemd-timesyncd
EOF
