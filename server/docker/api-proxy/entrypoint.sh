#!/bin/bash
set -euo pipefail

echo "📡 Bringing up all WireGuard interfaces from /etc/wireguard..."

shopt -s nullglob
conf_files=(/etc/wireguard/*.conf)
shopt -u nullglob

if [[ ${#conf_files[@]} -eq 0 ]]; then
    echo "⚠️ No WireGuard config files found in /etc/wireguard. Skipping interface bring-up."
else
    for conf in "${conf_files[@]}"; do
        iface=$(basename "$conf" .conf)
        if ip link show "$iface" &>/dev/null; then
            echo "🔄 Interface '$iface' is already active."
        else
            echo "🚀 Bringing up interface '$iface'..."
            wg-quick up "$iface" || echo "⚠️ Failed to bring up '$iface'"
        fi
    done
fi

echo "🔍 Current WireGuard state:"
wg

echo "📦 Starting nginx..."
exec nginx -g "daemon off;"
