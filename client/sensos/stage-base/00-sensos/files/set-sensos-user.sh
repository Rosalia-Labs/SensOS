#!/bin/bash

# Check if SENSOS_USER is already set in /etc/environment
if grep -q "^SENSOS_USER=" /etc/environment; then
    echo "SENSOS_USER is already set. Skipping detection."
    exit 0
fi

# Find the first non-root user (UID >= 1000)
FIRST_USER=$(getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 {print $1; exit}')

# Ensure we found a user
if [[ -z "$FIRST_USER" ]]; then
    echo "Error: No non-root user found!"
    exit 1
fi

# Append SENSOS_USER to /etc/environment
echo "SENSOS_USER=$FIRST_USER" >>/etc/environment

# Reload systemd environment so future services pick up SENSOS_USER
systemctl daemon-reexec

echo "SENSOS_USER set to $FIRST_USER"
