#!/usr/bin/env bash
# cache-sys-info.sh
# Writes identifying information into /sensos/data/device-info.txt (by default).
# Uses /sensos/lib/parse-switches.sh for CLI parsing.
# Safe by default: does NOT dump /sensos/etc contents.
# Optional: --include "<glob1> [glob2 ...]" to include specific files (with redaction).

set -euo pipefail

script_name=$(basename "$0")

# --- CLI ---------------------------------------------------------------

# Shell lib providing: register_option, parse_switches, show_usage
source /sensos/lib/parse-switches.sh

# Options (defaults keep behavior identical to earlier safe version)
register_option --data-dir      data-dir      "Target directory for the info file"                "/sensos/data"
register_option --info-file     info-file     "File name to write inside --data-dir"              "device-info.txt"
register_option --include       include       "Space-separated globs of files to include (redacted)" ""
register_option --redact        redact        "Redact common secrets in included files"           "true"

# Parse CLI
parse_switches "$script_name" "$@"

# Map to safe var names created by the lib
DATA_DIR="${data_dir}"
INFO_FILE_NAME="${info_file}"
INCLUDE_STRING="${include}"
REDACT="${redact}"

# --- Prep --------------------------------------------------------------

INFO_PATH="${DATA_DIR%/}/${INFO_FILE_NAME}"
TMP_FILE="$(mktemp)"

# Ensure the target dir exists (ownership/permissions handled by caller if needed)
sudo mkdir -p "$DATA_DIR"

# --- Collect metadata --------------------------------------------------

HOSTNAME_VAL="$(hostname || true)"
MACHINE_ID="$(cat /etc/machine-id 2>/dev/null || true)"
CPU_SERIAL="$(awk '/Serial/ {print $3}' /proc/cpuinfo 2>/dev/null || true)"
CREATED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

{
  echo "Device Hostname: ${HOSTNAME_VAL:-unknown}"
  echo "Device Machine ID: ${MACHINE_ID:-unknown}"
  [[ -n "${CPU_SERIAL:-}" ]] && echo "CPU Serial: $CPU_SERIAL"
  echo "Created At: $CREATED_AT"

  # Optional include list (whitelist) — still redacted by default
  if [[ -n "${INCLUDE_STRING// /}" ]]; then
    echo "--- Included config (redacted=${REDACT}) ---"
    # Split the single string into an array (space-separated)
    IFS=' ' read -r -a __include_globs <<< "$INCLUDE_STRING"
    shopt -s nullglob
    for pat in "${__include_globs[@]}"; do
      for cfg in $pat; do
        [[ -f "$cfg" ]] || continue
        echo "### $(basename "$cfg") ###"
        if [[ "$REDACT" == "true" ]]; then
          # Redact common secret-like keys in env/ini/yaml/json-ish text.
          # Masks values after ':' or '=' or inside JSON quotes.
          sed -E \
            -e 's/((?i)password|passwd|secret|token|api[_-]?key|client[_-]?secret|access[_-]?key|private[_-]?key)([[:space:]]*[:=][[:space:]]*)[^#[:space:]]+/\1\2REDACTED/g' \
            -e 's/("((?i)password|passwd|secret|token|api[_-]?key|client[_-]?secret|access[_-]?key|private[_-]?key)"[[:space:]]*:[[:space:]]*)".*"/\1"REDACTED"/g' \
            "$cfg"
        else
          cat "$cfg"
        fi
      done
    done
    shopt -u nullglob
  fi
} > "$TMP_FILE"

# Atomic move into place
sudo mv "$TMP_FILE" "$INFO_PATH"
echo "✅ Wrote info to $INFO_PATH"
