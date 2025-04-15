#!/bin/bash
set -euo pipefail

WG_CONFIG_DIR="/config/wg_confs"

refresh_status() {
    for iface in $(wg show interfaces); do
        wg show "$iface" >"/config/wireguard_status_${iface}.txt" || true
    done
}

trap 'refresh_status' SIGUSR1

mkdir -p /etc/wireguard
chown root:root /etc/wireguard
chmod 0700 /etc/wireguard

for config_file in "$WG_CONFIG_DIR"/*.conf; do
    [ -e "$config_file" ] || continue

    vpn=$(basename "$config_file" .conf)
    dest="/etc/wireguard/$vpn.conf"

    cp "$config_file" "$dest" || true
    chown root:root "$dest" || true
    chmod 0600 "$dest" || true

    echo "🚀 Bringing up interface $vpn"
    wg-quick up "$vpn" || echo "⚠️ Failed to bring up $vpn"
done

rm -f /config/wireguard_status_*.txt
refresh_status

while true; do sleep 3600; done
