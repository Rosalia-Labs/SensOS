#!/bin/bash -e

sudo mkdir -p /sensos/data
sudo mkdir -p /sensos/log
sudo mkdir -p /sensos/etc

sudo chown -R sensos-admin:sensos-data /sensos
sudo chmod -R g+ws /sensos
sudo chmod -R +rX /sensos
