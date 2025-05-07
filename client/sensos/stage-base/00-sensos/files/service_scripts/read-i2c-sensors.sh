#!/bin/bash
set -euo pipefail

timestamp=$(date -u +"%Y%m%dT%H%M%SZ")
output_dir="/sensos/data/sensor_readings"
output_file="${output_dir}/${timestamp}.json"
config_file="/sensos/etc/i2c-sensors.conf"

mkdir -p "$output_dir"
source "$config_file"

read_bme280() {
    case "$BME280_ADDR" in
    1) addr="0x76" ;;
    2) addr="0x77" ;;
    3) addr="both" ;;
    *) return 0 ;;
    esac

    # Only supporting one sensor for now
    if [[ "$addr" == "both" ]]; then addr="0x76"; fi

    python3 - <<EOF
import json
import board
import adafruit_bme280
import busio

i2c = busio.I2C(board.SCL, board.SDA)
bme280 = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=${addr})
print(json.dumps({
    "temperature_c": round(bme280.temperature, 2),
    "humidity_percent": round(bme280.humidity, 2),
    "pressure_hpa": round(bme280.pressure, 2),
}))
EOF
}

# Future stubs for other sensors
read_scd30() { echo "null"; }
read_scd4x() { echo "null"; }
read_ads1015() { echo "null"; }
read_i2c_gps() { echo "null"; }

bme280_data=$(read_bme280 || echo "null")
scd30_data=$(read_scd30)
scd4x_data=$(read_scd4x)
ads1015_data=$(read_ads1015)
i2c_gps_data=$(read_i2c_gps)

cat >"$output_file" <<EOF
{
  "timestamp": "$(date -u --iso-8601=seconds)",
  "bme280": $bme280_data,
  "scd30": $scd30_data,
  "scd4x": $scd4x_data,
  "ads1015": $ads1015_data,
  "i2c_gps": $i2c_gps_data
}
EOF

echo "Wrote sensor reading to $output_file"
