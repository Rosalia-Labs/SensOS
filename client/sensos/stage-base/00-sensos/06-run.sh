# Lets not leave the keys dir around just in case
on_chroot <<EOF
rm -rf /sensos/keys || true
EOF
