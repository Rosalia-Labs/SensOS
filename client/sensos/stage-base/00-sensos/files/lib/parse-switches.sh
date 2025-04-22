#!/bin/bash

# Global maps for help text and defaults
declare -A __cli_options_help
declare -A __cli_options_defaults

# Register an option: register_option "--option-name" VAR_NAME "Help text" "default"
register_option() {
    local opt="$1"
    local varname="$2"
    local help="$3"
    local default="$4"

    local safe_varname="${varname//-/_}"
    __cli_options_help["$opt"]="$help"
    __cli_options_defaults["$safe_varname"]="$default"

    # Set default value if not already defined
    if [[ -z "${!safe_varname+x}" ]]; then
        declare -g "$safe_varname=$default"
    fi
}

# Parse CLI switches
parse_switches() {
    local script_name="$1"
    shift

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
        --*) # --key value
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
        safe_varname="${varname//-/_}"

        if [[ -v __cli_options_help["$opt"] ]]; then
            declare -g "$safe_varname=$val"
        else
            echo "Unknown option: $opt"
            show_usage "$script_name"
            exit 1
        fi
        shift
    done
}

# Show usage/help
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
        printf "  %-25s %-40s %s\n" "$opt <value>" "$help" "(default: $default)"
    done
    echo "  --help                   Show this help message"
}
