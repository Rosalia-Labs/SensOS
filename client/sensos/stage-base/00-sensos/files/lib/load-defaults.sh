# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

# /sensos/lib/load-defaults.sh
# Usage: load_defaults /path/to/defaults.conf script-name-without-.sh

load_defaults() {
    local file="$1"
    local caller_name="$2"

    local section="${caller_name%.sh}"
    section="${section//-/_}"

    [ -f "$file" ] || {
        echo "Missing file: $file"
        return 0
    }

    local current_section=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%#*}"
        line="${line%%;*}"
        line="$(echo "$line" | xargs)"
        [ -z "$line" ] && continue

        if [[ "$line" =~ ^\[(.*)\]$ ]]; then
            current_section="${BASH_REMATCH[1]}"
        elif [[ "$line" == *=* ]]; then
            if [[ "$current_section" == "global" || "$current_section" == "$section" ]]; then
                local key="${line%%=*}"
                local value="${line#*=}"
                key="$(echo "$key" | tr '-' '_' | xargs)"
                value="$(echo "$value" | xargs)"
                value="${value%\"}"
                value="${value#\"}"
                eval "$key=\"\$value\""
            fi
        fi
    done <"$file"
}
