#!/bin/bash

set -euo pipefail

WG_CONFIG_DIR="/config/wg_confs"

mkdir -p /etc/wireguard
chown root:root /etc/wireguard
chmod 0700 /etc/wireguard

while true; do
    for config_file in "$WG_CONFIG_DIR"/*.conf; do
        [ -e "$config_file" ] || continue

        vpn=$(basename "$config_file" .conf)
        dest="/etc/wireguard/$vpn.conf"

        # Use || true to avoid breaking the loop
        mv -f "$config_file" "$dest" || true
        chown root:root "$dest" || true
        chmod 0600 "$dest" || true

        if wg show "$vpn" &>/dev/null; then
            echo "🔄 Updating existing interface $vpn"
            tmpfile=$(mktemp)
            if wg-quick strip "$vpn" >"$tmpfile"; then
                wg syncconf "$vpn" <"$tmpfile" || echo "⚠️ Failed to syncconf $vpn"
            else
                echo "⚠️ Failed to strip $vpn"
            fi
            rm -f "$tmpfile"
        else
            echo "🚀 Bringing up interface $vpn"
            wg-quick up "$vpn" || echo "⚠️ Failed to bring up $vpn"
        fi
    done

    # Always try to write status; don't crash if it fails
    wg show >/config/wireguard_status || true

    sleep 10
done
