#!/bin/bash
set -e

REQUIRED_DEBS="${REQUIRED_DEBS:-}"
if [ -z "$REQUIRED_DEBS" ]; then
    echo "❌ REQUIRED_DEBS is not set. Aborting."
    exit 1
fi

echo "📦 Installing from local /debs if available..."
for pkg in $REQUIRED_DEBS; do
    deb_path=$(find /debs -type f -name "${pkg}_*.deb" | sort -V | tail -n 1)
    if [ -n "$deb_path" ]; then
        echo "📦 Installing $pkg from $deb_path"
        dpkg -i "$deb_path" || true
    else
        echo "⚠️ No local .deb found for $pkg"
    fi
done

echo "🔍 Checking for missing packages..."
MISSING=""
for pkg in $REQUIRED_DEBS; do
    dpkg -s "$pkg" >/dev/null 2>&1 || MISSING="$MISSING $pkg"
done

if [ -n "$MISSING" ]; then
    echo "🌐 Installing missing packages via APT: $MISSING"
    apt-get update
    apt-get install -y --no-install-recommends $MISSING
    rm -rf /var/lib/apt/lists/*
else
    echo "✅ All packages installed from local .debs"
fi
