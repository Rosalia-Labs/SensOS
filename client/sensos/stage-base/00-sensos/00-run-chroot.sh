#!/bin/bash -e

# Update the CA certificates in the target system
update-ca-certificates

# Enable i2c
raspi-config nonint do_i2c 0
