#!/bin/bash
set -euo pipefail

INIT_DIR="/sensos/init.d"
LOG_DIR="/sensos/log"
LOG_FILE="${LOG_DIR}/init.log"

mkdir -p "$LOG_DIR"

if [ ! -d "$INIT_DIR" ]; then
    echo "No init directory at $INIT_DIR — skipping." | tee -a "$LOG_FILE"
    exit 0
fi

shopt -s nullglob
for script in "$INIT_DIR"/*; do
    if [ -x "$script" ]; then
        echo "[INIT] Running $script" | tee -a "$LOG_FILE"
        if "$script" >>"$LOG_FILE" 2>&1; then
            echo "[INIT] Success — deleting $script" | tee -a "$LOG_FILE"
            rm -f "$script"
        else
            echo "[INIT] Error in $script — keeping it for retry" | tee -a "$LOG_FILE"
        fi
    fi
done
