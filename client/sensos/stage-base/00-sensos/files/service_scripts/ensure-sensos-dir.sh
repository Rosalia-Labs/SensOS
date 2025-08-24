#!/bin/bash -e
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

sudo mkdir -p /sensos/data
sudo mkdir -p /sensos/log
sudo mkdir -p /sensos/etc

sudo chown -R sensos-admin:sensos-data /sensos
sudo chmod -R g+ws /sensos
sudo chmod -R +rX /sensos
