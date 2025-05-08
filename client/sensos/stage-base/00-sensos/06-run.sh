on_chroot <<EOF
systemctl enable create-python-venv
install -m 644 /sensos/etc/chrony.conf /etc/chrony/chrony.conf
systemctl enable chrony
systemctl disable systemd-timesyncd
EOF
