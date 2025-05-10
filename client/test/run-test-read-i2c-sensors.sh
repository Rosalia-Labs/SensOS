#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_FILE="$SCRIPT_DIR/../sensos/stage-base/00-sensos/files/service-scripts/read-i2c-sensors.py"

# Check: must be a file
if [[ ! -f "$SCRIPT_FILE" ]]; then
    echo "❌ Error: $SCRIPT_FILE is not a file (might be a directory)"
    exit 1
fi

docker run --rm \
    -v "$SCRIPT_FILE":/orig/read-i2c-sensors.py:ro \
    -w /test \
    python:3.11-slim bash -c $'\
    set -e\n\
    pip install -q pytest\n\
\n\
    mkdir -p /sensos/etc /sensos/data/microenv /test\n\
    cat > /sensos/etc/i2c-sensors.conf <<EOF\n\
BME280_0x76_INTERVAL_SEC=1\n\
ADS1015_INTERVAL_SEC=2\n\
EOF\n\
\n\
    awk \'\n\
      /^def read_bme280/ {\n\
        print "def read_bme280(addr_str=None):"\n\
        print "    return {\\\"temperature_c\\\": 20.0, \\\"humidity_percent\\\": 50.0, \\\"pressure_hpa\\\": 1013.25}"\n\
        skip=1; next\n\
      }\n\
      /^def read_ads1015/ {\n\
        print "def read_ads1015(addr_str=None):"\n\
        print "    return {\\\"A0\\\": 1.23, \\\"A1\\\": 2.34, \\\"A2\\\": 3.45, \\\"A3\\\": 4.56}"\n\
        skip=1; next\n\
      }\n\
      skip { if (/^def /) { skip=0 }; next }\n\
      { print }\n\
    \' /orig/read-i2c-sensors.py > /test/read-i2c-sensors.py\n\
\n\
    chmod +x /test/read-i2c-sensors.py\n\
    python3 /test/read-i2c-sensors.py &\n\
    PID=$!\n\
    sleep 6\n\
    kill $PID || true\n\
\n\
    echo "✅ Dumping /sensos/data/microenv/sensor_readings.db contents:"\n\
    sqlite3 /sensos/data/microenv/sensor_readings.db <<SQL\n\
.headers on\n\
.mode column\n\
SELECT timestamp, device_address, sensor_type, key, value FROM i2c_readings;\n\
SQL\n\
'
