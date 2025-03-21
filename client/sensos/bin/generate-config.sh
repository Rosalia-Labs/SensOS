#!/bin/bash

# Compute the absolute path to the directory of the script
SCRIPT_DIR="$(cd "$(dirname \"${BASH_SOURCE[0]}\")/.." && pwd)"

# Compute the path to the target config file
CONFIG_FILE="${SCRIPT_DIR}/../pi-gen/config"

# Default values for always-included variables
PI_GEN_RELEASE="SensOS reference"
PIGEN_DOCKER_OPTS="-v $SCRIPT_DIR:/sensos"
SENSOS_STAGES=$(echo "stage-base" | awk '{for(i=1;i<=NF;i++) $i="/sensos/"$i}1')
STAGE_LIST="stage0 stage1 $SENSOS_STAGES stage2"
IMG_NAME="sensos"
TIMEZONE_DEFAULT="UTC"
KEYBOARD_KEYMAP="us"
KEYBOARD_LAYOUT="English (US)"
LOCALE_DEFAULT="C.UTF-8"
FIRST_USER_NAME="sensos"
FIRST_USER_PASS="sensos"
DISABLE_FIRST_BOOT_USER_RENAME="1"
WPA_COUNTRY="US"
DEPLOY_COMPRESSION="none"
ENABLE_FIRSTBOOT_WIFI_AP="0"
ENABLE_FIRSTBOOT_EEPROM="0"

# Initialize an array to store configuration lines
CONFIG=(
    "PI_GEN_RELEASE=\"$PI_GEN_RELEASE\""
    "PIGEN_DOCKER_OPTS=\"$PIGEN_DOCKER_OPTS\""
    "SENSOS_STAGES=\"$SENSOS_STAGES\""
    "STAGE_LIST=\"$STAGE_LIST\""
    "IMG_NAME=\"$IMG_NAME\""
    "TIMEZONE_DEFAULT=\"$TIMEZONE_DEFAULT\""
    "KEYBOARD_KEYMAP=\"$KEYBOARD_KEYMAP\""
    "KEYBOARD_LAYOUT=\"$KEYBOARD_LAYOUT\""
    "LOCALE_DEFAULT=\"$LOCALE_DEFAULT\""
    "FIRST_USER_NAME=\"$FIRST_USER_NAME\""
    "FIRST_USER_PASS=\"$FIRST_USER_PASS\""
    "DISABLE_FIRST_BOOT_USER_RENAME=\"$DISABLE_FIRST_BOOT_USER_RENAME\""
    "WPA_COUNTRY=\"$WPA_COUNTRY\""
    "DEPLOY_COMPRESSION=\"$DEPLOY_COMPRESSION\""
    "ENABLE_FIRSTBOOT_WIFI_AP=\"$ENABLE_FIRSTBOOT_WIFI_AP\""
    "ENABLE_FIRSTBOOT_EEPROM=\"$ENABLE_FIRSTBOOT_EEPROM\""
)

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
    --enable-firstboot-wifi-ap)
        ENABLE_FIRSTBOOT_WIFI_AP="$2"
        CONFIG[14]="ENABLE_FIRSTBOOT_WIFI_AP=\"$ENABLE_FIRSTBOOT_WIFI_AP\""
        shift 2
        ;;
    --enable-firstboot-eeprom)
        ENABLE_FIRSTBOOT_EEPROM="$2"
        CONFIG[15]="ENABLE_FIRSTBOOT_EEPROM=\"$ENABLE_FIRSTBOOT_EEPROM\""
        shift 2
        ;;
    --wpa-country)
        WPA_COUNTRY="$2"
        CONFIG[12]="WPA_COUNTRY=\"$WPA_COUNTRY\""
        shift 2
        ;;
    --deploy-compression)
        DEPLOY_COMPRESSION="$2"
        CONFIG[13]="DEPLOY_COMPRESSION=\"$DEPLOY_COMPRESSION\""
        shift 2
        ;;
    # Include other existing arguments here without modification
    *)
        echo "Unknown option: $1" >&2
        exit 1
        ;;
    esac
done

# Ensure the target directory exists
mkdir -p "$(dirname \"$CONFIG_FILE\")"

echo
echo "Settings:"
echo

# Write configuration to file
{
    printf "%s\n" "${CONFIG[@]}"
} | tee "$CONFIG_FILE"

echo
echo "Config file written. Now go to the pi-gen directory and run ./build-docker.sh"
echo
