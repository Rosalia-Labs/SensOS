#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_FILE="$SCRIPT_DIR/../../sensos/stage-base/00-sensos/files/service_scripts/read-i2c-sensors.py"
LIB_DIR="$SCRIPT_DIR/../../sensos/stage-base/00-sensos/files/lib"

docker run --rm \
  -v "$SCRIPT_FILE":/orig/read-i2c-sensors.py:ro \
  -v "$LIB_DIR":/sensos_lib:ro \
  -e PYTHONPATH="/sensos_lib" \
  -w /test \
  python:3.11-slim bash -c $'set -e
apt-get update -qq && apt-get install -y -qq sqlite3 >/dev/null
pip install -q requests

mkdir -p /sensos/etc /sensos/data/microenv /test
cat > /sensos/etc/i2c-sensors.conf <<CONF
BME280_0x76_INTERVAL_SEC=1
ADS1015_INTERVAL_SEC=2
CONF

awk \'
/^def read_bme280/ {
  print "def read_bme280(addr_str=None):"
  print "    return {\\"temperature_c\\": 20.0, \\"humidity_percent\\": 50.0, \\"pressure_hpa\\": 1013.25}"
  skip=1; next
}
/^def read_ads1015/ {
  print "def read_ads1015(addr_str=None):"
  print "    return {\\"A0\\": 1.23, \\"A1\\": 2.34, \\"A2\\": 3.45, \\"A3\\": 4.56}"
  skip=1; next
}
skip && /^def / { skip=0 }
!skip
\' /orig/read-i2c-sensors.py > /test/read-i2c-sensors.py

chmod +x /test/read-i2c-sensors.py
python3 /test/read-i2c-sensors.py &
PID=$!
sleep 6
kill $PID || true

echo "✅ Dumping /sensos/data/microenv/i2c_readings.db contents:"
sqlite3 /sensos/data/microenv/i2c_readings.db <<SQL
.headers on
.mode column
SELECT timestamp, device_address, sensor_type, key, value FROM i2c_readings;
SQL

row_count=$(sqlite3 /sensos/data/microenv/i2c_readings.db "SELECT COUNT(*) FROM i2c_readings;")
if [ "$row_count" -lt 10 ]; then
  echo "❌ Test failed: expected at least 10 rows, got $row_count"
  exit 1
else
  echo "✅ Test passed: $row_count rows written"
fi
'
