#!/bin/bash
set -euo pipefail

INIT_DIR="/sensos/init.d"

if [ ! -d "$INIT_DIR" ]; then
    echo "No init directory at $INIT_DIR — skipping."
    exit 0
fi

shopt -s nullglob
for script in "$INIT_DIR"/*; do
    if [ -x "$script" ]; then
        echo "[INIT] Running $script"
        if "$script"; then
            echo "[INIT] Success — deleting $script"
            rm -f "$script"
        else
            echo "[INIT] Error in $script — keeping it for retry"
        fi
    fi
done
