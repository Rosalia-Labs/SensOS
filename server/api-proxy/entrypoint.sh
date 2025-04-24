#!/bin/bash
set -euo pipefail

echo "📡 Bringing up all WireGuard interfaces from /etc/wireguard..."

for conf in /etc/wireguard/*.conf; do
    iface=$(basename "$conf" .conf)
    if ip link show "$iface" &>/dev/null; then
        echo "🔄 Interface '$iface' is already active."
    else
        echo "🚀 Bringing up interface '$iface'..."
        wg-quick up "$iface" || echo "⚠️ Failed to bring up '$iface'"
    fi
done

echo "🔍 Current WireGuard state:"
wg

echo "📦 Starting nginx..."
exec nginx -g "daemon off;"
