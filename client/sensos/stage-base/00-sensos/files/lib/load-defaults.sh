#!/bin/bash
# load-defaults.sh: Load default variable values from an INI-style config file.

# Usage: load_defaults "/sensos/etc/defaults.conf" "config-arecord.sh"

load_defaults() {
    local file="$1"
    local caller_name="$2"

    # Normalize: config-arecord.sh → config_arecord
    local section
    section=$(basename "$caller_name" | sed 's/\.sh$//' | tr '-' '_')

    [ -f "$file" ] || return 0

    local current_section=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Strip comments and whitespace
        line="${line%%#*}"
        line="${line%%;*}"
        line="$(echo "$line" | xargs)"
        [ -z "$line" ] && continue

        if [[ "$line" =~ \[(.*)\] ]]; then
            current_section="${BASH_REMATCH[1]}"
        elif [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_-]*= ]]; then
            if [[ "$current_section" == "global" || "$current_section" == "$section" ]]; then
                key="${line%%=*}"
                value="${line#*=}"

                # Normalize: convert dashes to underscores in key
                key="$(echo "$key" | xargs | tr '-' '_')"
                value="$(echo "$value" | xargs | sed -e 's/^"//' -e 's/"$//')"

                # Assign as variable
                eval "$key=\"\$value\""
            fi
        fi
    done <"$file"
}
