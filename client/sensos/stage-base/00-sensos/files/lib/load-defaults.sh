#!/bin/bash
# load-defaults.sh: Load default variable values from an INI-style config file.

# Usage: load_defaults "/sensos/etc/defaults.conf" "config-arecord"

load_defaults() {
    local file="$1"
    local section="$2"

    [ -f "$file" ] || return 0

    # Load global section
    local current_section=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%#*}" # strip comments
        line="${line%%;*}"
        line="$(echo "$line" | xargs)" # trim whitespace
        [ -z "$line" ] && continue

        if [[ "$line" =~ \[(.*)\] ]]; then
            current_section="${BASH_REMATCH[1]}"
        elif [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            if [[ "$current_section" == "global" || "$current_section" == "$section" ]]; then
                eval "$line"
            fi
        fi
    done <"$file"
}
