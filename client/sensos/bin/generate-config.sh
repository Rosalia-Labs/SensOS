#!/bin/bash

# Compute the absolute path to the current directory (sensos/)
SENSOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Default values for always-included variables
PI_GEN_RELEASE="SensOS reference"
PIGEN_DOCKER_OPTS="-v $SENSOS_DIR:/sensos"
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
)

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
    --pi-gen-release)
        PI_GEN_RELEASE="$2"
        CONFIG[0]="PI_GEN_RELEASE=\"$PI_GEN_RELEASE\""
        shift 2
        ;;
    --img-name)
        IMG_NAME="$2"
        CONFIG[4]="IMG_NAME=\"$IMG_NAME\""
        shift 2
        ;;
    --release)
        CONFIG+=("RELEASE=\"$2\"")
        shift 2
        ;;
    --apt-proxy)
        CONFIG+=("APT_PROXY=\"$2\"")
        shift 2
        ;;
    --work-dir)
        CONFIG+=("WORK_DIR=\"$2\"")
        shift 2
        ;;
    --deploy-dir)
        CONFIG+=("DEPLOY_DIR=\"$2\"")
        shift 2
        ;;
    --deploy-compression)
        CONFIG+=("DEPLOY_COMPRESSION=\"$2\"")
        shift 2
        ;;
    --compression-level)
        CONFIG+=("COMPRESSION_LEVEL=\"$2\"")
        shift 2
        ;;
    --use-qemu)
        CONFIG+=("USE_QEMU=\"$2\"")
        shift 2
        ;;
    --locale-default)
        LOCALE_DEFAULT="$2"
        CONFIG[8]="LOCALE_DEFAULT=\"$LOCALE_DEFAULT\""
        shift 2
        ;;
    --target-hostname)
        CONFIG+=("TARGET_HOSTNAME=\"$2\"")
        shift 2
        ;;
    --keyboard-keymap)
        KEYBOARD_KEYMAP="$2"
        CONFIG[6]="KEYBOARD_KEYMAP=\"$KEYBOARD_KEYMAP\""
        shift 2
        ;;
    --keyboard-layout)
        KEYBOARD_LAYOUT="$2"
        CONFIG[7]="KEYBOARD_LAYOUT=\"$KEYBOARD_LAYOUT\""
        shift 2
        ;;
    --timezone-default)
        TIMEZONE_DEFAULT="$2"
        CONFIG[5]="TIMEZONE_DEFAULT=\"$TIMEZONE_DEFAULT\""
        shift 2
        ;;
    --first-user-name)
        FIRST_USER_NAME="$2"
        CONFIG[9]="FIRST_USER_NAME=\"$FIRST_USER_NAME\""
        shift 2
        ;;
    --first-user-pass)
        FIRST_USER_PASS="$2"
        CONFIG[10]="FIRST_USER_PASS=\"$FIRST_USER_PASS\""
        shift 2
        ;;
    --disable-first-boot-user-rename)
        DISABLE_FIRST_BOOT_USER_RENAME="$2"
        CONFIG[11]="DISABLE_FIRST_BOOT_USER_RENAME=\"$DISABLE_FIRST_BOOT_USER_RENAME\""
        shift 2
        ;;
    --wpa-country)
        CONFIG+=("WPA_COUNTRY=\"$2\"")
        shift 2
        ;;
    --enable-ssh)
        CONFIG+=("ENABLE_SSH=\"$2\"")
        shift 2
        ;;
    --pubkey-ssh-first-user)
        CONFIG+=("PUBKEY_SSH_FIRST_USER=\"$2\"")
        shift 2
        ;;
    --pubkey-only-ssh)
        CONFIG+=("PUBKEY_ONLY_SSH=\"$2\"")
        shift 2
        ;;
    --setfcap)
        CONFIG+=("SETFCAP=\"$2\"")
        shift 2
        ;;
    --stage-list)
        STAGE_LIST="$2"
        CONFIG[3]="STAGE_LIST=\"$STAGE_LIST\""
        shift 2
        ;;
    --sensos-stages)
        SENSOS_STAGES=$(echo "$2" | awk '{for(i=1;i<=NF;i++) $i="/sensos/"$i}1')
        CONFIG[2]="SENSOS_STAGES=\"$SENSOS_STAGES\""
        CONFIG[3]="STAGE_LIST=\"stage0 stage1 $SENSOS_STAGES stage2\""
        shift 2
        ;;
    --export-config-dir)
        CONFIG+=("EXPORT_CONFIG_DIR=\"$2\"")
        shift 2
        ;;
    --help)
        echo "Usage: $0 [options]"
        echo "Options:"
        echo "  --pi-gen-release <name>             Override PI_GEN_RELEASE (default: \"$PI_GEN_RELEASE\")"
        echo "  --img-name <name>                   Set image name (default: \"$IMG_NAME\")"
        echo "  --release <name>                    Set Debian release"
        echo "  --apt-proxy <url>                   Set APT proxy"
        echo "  --work-dir <path>                   Set work directory"
        echo "  --deploy-dir <path>                 Set deployment directory"
        echo "  --deploy-compression <type>         Set compression type (none, zip, gz, xz)"
        echo "  --compression-level <0-9>           Set compression level"
        echo "  --use-qemu <0|1>                    Enable QEMU mode"
        echo "  --locale-default <locale>           Set system locale (default: \"$LOCALE_DEFAULT\")"
        echo "  --target-hostname <hostname>        Set hostname"
        echo "  --keyboard-keymap <map>             Set keyboard keymap (default: \"$KEYBOARD_KEYMAP\")"
        echo "  --keyboard-layout <layout>          Set keyboard layout (default: \"$KEYBOARD_LAYOUT\")"
        echo "  --timezone-default <timezone>       Set timezone (default: \"$TIMEZONE_DEFAULT\")"
        echo "  --first-user-name <name>            Set first user name"
        echo "  --first-user-pass <password>        Set first user password"
        echo "  --disable-first-boot-user-rename <0|1> Disable first boot user rename"
        echo "  --wpa-country <code>                Set WLAN country code"
        echo "  --enable-ssh <0|1>                  Enable SSH"
        echo "  --pubkey-ssh-first-user <key>       Set SSH public key"
        echo "  --pubkey-only-ssh <0|1>             Disable SSH password login"
        echo "  --setfcap <0|1>                     Set file capabilities"
        echo "  --sensos-stages <stages>            Override SENSOS_STAGES (default: \"$SENSOS_STAGES\")"
        echo "  --stage-list <stages>               Override STAGE_LIST (default: \"stage0 stage1 /sensos/\$SENSOS_STAGES stage2\")"
        echo "  --export-config-dir <path>          Set export config directory"
        echo "  --help                              Show this help message"
        exit 0
        ;;
    *)
        echo "Unknown option: $1" >&2
        exit 1
        ;;
    esac
done

if [ -f "$SENSOS_DIR/../../server/.registry_auth/domain.crt" ]; then
    cp -a "$SENSOS_DIR/../../server/.registry_auth/domain.crt" "$SENSOS_DIR/stage-base/00-sensos/files"
else
    echo "Registry certificate not found. Be sure to configure the server before the client." >&2
fi

# Emit configuration to stdout
echo "# Auto-generated configuration file"
printf "%s\n" "${CONFIG[@]}"
