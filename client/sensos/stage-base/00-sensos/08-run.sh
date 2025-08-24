#!/bin/bash -e
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

# 08-run.sh: Configure persistent systemd-journald logs.
on_chroot <<EOF
echo Storage=persistent > /etc/systemd/journald.conf
EOF
