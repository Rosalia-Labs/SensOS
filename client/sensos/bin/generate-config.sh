#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../pi-gen/config"

# Default settings
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

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
    --pi-gen-release)
        PI_GEN_RELEASE="$2"
        shift 2
        ;;
    --pigen-docker-opts)
        PIGEN_DOCKER_OPTS="$2"
        shift 2
        ;;
    --sensos-stages)
        SENSOS_STAGES="$2"
        shift 2
        ;;
    --stage-list)
        STAGE_LIST="$2"
        shift 2
        ;;
    --img-name)
        IMG_NAME="$2"
        shift 2
        ;;
    --timezone-default)
        TIMEZONE_DEFAULT="$2"
        shift 2
        ;;
    --keyboard-keymap)
        KEYBOARD_KEYMAP="$2"
        shift 2
        ;;
    --keyboard-layout)
        KEYBOARD_LAYOUT="$2"
        shift 2
        ;;
    --locale-default)
        LOCALE_DEFAULT="$2"
        shift 2
        ;;
    --first-user-name)
        FIRST_USER_NAME="$2"
        shift 2
        ;;
    --first-user-pass)
        FIRST_USER_PASS="$2"
        shift 2
        ;;
    --disable-first-boot-user-rename)
        DISABLE_FIRST_BOOT_USER_RENAME="$2"
        shift 2
        ;;
    --wpa-country)
        WPA_COUNTRY="$2"
        shift 2
        ;;
    --deploy-compression)
        DEPLOY_COMPRESSION="$2"
        shift 2
        ;;
    --enable-firstboot-wifi-ap)
        ENABLE_FIRSTBOOT_WIFI_AP="1"
        shift 1
        ;;
    --enable-firstboot-geekworm-ups)
        ENABLE_FIRSTBOOT_GEEKWORM_EEPROM="1"
        shift 1
        ;;
    --help)
        echo "Usage: $0 [options]"
        echo
        echo "Options:"
        echo "  --pi-gen-release <value>                   (default: $PI_GEN_RELEASE)"
        echo "  --pigen-docker-opts <value>                (default: $PIGEN_DOCKER_OPTS)"
        echo "  --sensos-stages <value>                    (default: $SENSOS_STAGES)"
        echo "  --stage-list <value>                       (default: $STAGE_LIST)"
        echo "  --img-name <value>                         (default: $IMG_NAME)"
        echo "  --timezone-default <value>                 (default: $TIMEZONE_DEFAULT)"
        echo "  --keyboard-keymap <value>                  (default: $KEYBOARD_KEYMAP)"
        echo "  --keyboard-layout <value>                  (default: $KEYBOARD_LAYOUT)"
        echo "  --locale-default <value>                   (default: $LOCALE_DEFAULT)"
        echo "  --first-user-name <value>                  (default: $FIRST_USER_NAME)"
        echo "  --first-user-pass <value>                  (default: $FIRST_USER_PASS)"
        echo "  --disable-first-boot-user-rename <0|1>     (default: $DISABLE_FIRST_BOOT_USER_RENAME)"
        echo "  --wpa-country <value>                      (default: $WPA_COUNTRY)"
        echo "  --deploy-compression <value>               (default: $DEPLOY_COMPRESSION)"
        echo "  --enable-firstboot-wifi-ap                 Enable first boot WiFi AP (default: disabled)"
        echo "  --enable-firstboot-geekworm-ups            Enable first boot EEPROM update (default: disabled)"
        echo "  --help                                     Display this help message"
        exit 0
        ;;
    *)
        echo "Unknown option: $1" >&2
        exit 1
        ;;
    esac
done

echo

# Write config
mkdir -p "$(dirname "$CONFIG_FILE")"

cat <<EOF | tee "$CONFIG_FILE"
PI_GEN_RELEASE="$PI_GEN_RELEASE"
PIGEN_DOCKER_OPTS="$PIGEN_DOCKER_OPTS"
SENSOS_STAGES="$SENSOS_STAGES"
STAGE_LIST="$STAGE_LIST"
IMG_NAME="$IMG_NAME"
TIMEZONE_DEFAULT="$TIMEZONE_DEFAULT"
KEYBOARD_KEYMAP="$KEYBOARD_KEYMAP"
KEYBOARD_LAYOUT="$KEYBOARD_LAYOUT"
LOCALE_DEFAULT="$LOCALE_DEFAULT"
FIRST_USER_NAME="$FIRST_USER_NAME"
FIRST_USER_PASS="$FIRST_USER_PASS"
DISABLE_FIRST_BOOT_USER_RENAME="$DISABLE_FIRST_BOOT_USER_RENAME"
WPA_COUNTRY="$WPA_COUNTRY"
DEPLOY_COMPRESSION="$DEPLOY_COMPRESSION"
ENABLE_FIRSTBOOT_WIFI_AP="$ENABLE_FIRSTBOOT_WIFI_AP"
ENABLE_FIRSTBOOT_GEEKWORM_EEPROM="$ENABLE_FIRSTBOOT_GEEKWORM_EEPROM"
EOF

echo -e "\nConfig file written. Now go to the pi-gen directory and run ./build-docker.sh\n"
