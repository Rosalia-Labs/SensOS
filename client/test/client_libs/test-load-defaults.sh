#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "$SCRIPT_DIR/../../sensos/stage-base/00-sensos/files/lib" && pwd)"

docker run --rm \
    -v "$LIB_DIR:/sensos/lib:ro" \
    debian:bookworm-slim bash -c '
set -euo pipefail

apt-get update -qq
apt-get install -y --no-install-recommends bash coreutils >/dev/null

cat > /defaults.conf <<EOF
[global]
postgres_user = global_user
postgres_password = global_pw

[config_i2c_sensors]
bme280 = 3
scd30 = true
ads1015 = false
EOF

bme280=""
scd30=""
ads1015=""
postgres_user=""
postgres_password=""

source /sensos/lib/load-defaults.sh
load_defaults /defaults.conf config-i2c-sensors

echo "Final values:"
echo "bme280=${bme280:-<unset>}"
echo "scd30=${scd30:-<unset>}"
echo "ads1015=${ads1015:-<unset>}"
echo "postgres_user=${postgres_user:-<unset>}"
echo "postgres_password=${postgres_password:-<unset>}"
'
