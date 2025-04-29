#!/bin/bash
set -euo pipefail

# where your Controller drops .conf files
WG_SOURCE_DIR="/wireguard_config"
# where wg-quick expects them and where we write status
WG_CONFIG_DIR="/etc/wireguard"

refresh_status() {
    # remove old status dumps
    rm -f "$WG_SOURCE_DIR"/wireguard_status_*.txt
    for iface in $(wg show interfaces); do
        wg show "$iface" >"$WG_SOURCE_DIR/wireguard_status_${iface}.txt" || true
    done
}

trap 'refresh_status' SIGUSR1

# ensure local config dir exists & is secure
mkdir -p "$WG_CONFIG_DIR"
chown root:root "$WG_CONFIG_DIR"
chmod 0700 "$WG_CONFIG_DIR"

# copy & bring up every .conf
for src in "$WG_SOURCE_DIR"/*.conf; do
    [ -e "$src" ] || continue
    name=$(basename "$src" .conf)
    dest="$WG_CONFIG_DIR/$name.conf"

    echo "📋 Installing config $name.conf"
    cp "$src" "$dest" &&
        chown root:root "$dest" &&
        chmod 0600 "$dest"

    echo "🚀 Bringing up interface $name"
    wg-quick up "$name" || echo "⚠️ Failed to bring up $name"
done

# initial status dump
refresh_status

# background refresher
(
    while true; do
        sleep 30
        refresh_status
    done
) &

wait
