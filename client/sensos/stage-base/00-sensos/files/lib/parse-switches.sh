#!/bin/bash

# CLI option registration and parsing
declare -A __cli_options_help
declare -A __cli_options_defaults

register_option() {
    local opt="$1"
    local varname="$2"
    local help="$3"
    local default="$4"

    local safe_varname="${varname//-/_}"
    __cli_options_help["$opt"]="$help"
    __cli_options_defaults["$safe_varname"]="$default"

    # Only assign default if not already set
    if [[ -z "${!safe_varname+x}" ]]; then
        declare -g "$safe_varname"
        printf -v "$safe_varname" '%s' "$default"
    fi
}

parse_switches() {
    local script_name="$1"
    shift

    local opt val varname safe_varname

    while [[ $# -gt 0 ]]; do
        case "$1" in
        --help)
            show_usage "$script_name"
            exit 0
            ;;
        --*=*) # --key=value
            opt="${1%%=*}"
            val="${1#*=}"
            ;;
        --*) # --key value or flag
            opt="$1"
            if [[ $# -gt 1 && "$2" != --* ]]; then
                val="$2"
                shift
            else
                val="true"
            fi
            ;;
        *)
            echo "[ERROR] Unknown argument: $1"
            show_usage "$script_name"
            exit 1
            ;;
        esac

        varname="${opt#--}"
        safe_varname="${varname//-/_}"

        if [[ -v __cli_options_help["$opt"] ]]; then
            printf -v "$safe_varname" '%s' "$val"
        else
            echo "[ERROR] Unknown option: $opt"
            show_usage "$script_name"
            exit 1
        fi
        shift
    done
}

show_usage() {
    local script_name="$1"
    echo "Usage: $script_name [options]"
    echo
    echo "Options:"
    for opt in "${!__cli_options_help[@]}"; do
        local varname="${opt#--}"
        local safe_varname="${varname//-/_}"
        local default="${__cli_options_defaults[$safe_varname]}"
        local help="${__cli_options_help[$opt]}"
        printf "  %-25s %-40s %s\n" "$opt [value]" "$help" "(default: $default)"
    done
    echo "  --help                   Show this help message"
}
