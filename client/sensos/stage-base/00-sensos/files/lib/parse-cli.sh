#!/bin/bash

# Call this to register valid options with help text and optional default value
# Example:
#   register_option "--postgres-db" "Set database name" "postgres"
#   register_option "--enable-service" "Enable systemd service" "true"
declare -A __cli_options_help
declare -A __cli_options_defaults

register_option() {
    local opt="$1"
    local help="$2"
    local default="${3:-}"

    local varname=${opt#--}
    __cli_options_help["$opt"]="$help"
    __cli_options_defaults["$varname"]="$default"

    # Set initial value
    declare -g "$varname=$default"
}

parse_switches() {
    local script_name="$1"
    shift

    while [[ $# -gt 0 ]]; do
        case "$1" in
        --help)
            show_usage "$script_name"
            exit 0
            ;;
        --*=*) # handle --key=value
            opt="${1%%=*}"
            val="${1#*=}"
            ;;
        --*) # handle --key value
            opt="$1"
            val="$2"
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            show_usage "$script_name"
            exit 1
            ;;
        esac

        varname="${opt#--}"
        if [[ -v __cli_options_help["$opt"] ]]; then
            declare -g "$varname=$val"
        else
            echo "Unknown option: $opt"
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
        local default="${__cli_options_defaults[$varname]}"
        local help="${__cli_options_help[$opt]}"
        printf "  %-25s %-40s %s\n" "$opt <value>" "$help" "(default: $default)"
    done
    echo "  --help                   Show this help message"
}
