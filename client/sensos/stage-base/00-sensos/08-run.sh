#!/bin/bash -e
# 08-run.sh: Configure persistent systemd-journald logs.
on_chroot <<EOF
echo Storage=persistent > /etc/systemd/journald.conf
EOF
