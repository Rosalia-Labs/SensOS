#!/bin/bash
set -euo pipefail

print_device_hints_and_exit() {
    echo "❌ No --device specified."
    echo ""
    echo "💡 Make sure your SD card or USB writer is inserted *and detected* by the system."
    echo "   (Note: some SD card readers only appear after the card is mounted.)"
    echo ""
    echo "🔎 Here is the current output of 'diskutil list':"
    echo ""
    diskutil list
    echo ""
    echo "🔧 When ready, re-run this script with:"
    echo "    ./burn-boot-image.sh --device /dev/rdiskN"
    echo ""
    echo "⚠️  Be absolutely sure of the disk number — writing to the wrong device can destroy your system."
    exit 1
}

# Parse arguments
DEVICE=""
while [ $# -gt 0 ]; do
    case "$1" in
    --device)
        DEVICE="$2"
        shift 2
        ;;
    *)
        echo "❌ Unknown argument: $1"
        exit 1
        ;;
    esac
done

# Require device specification
if [ -z "$DEVICE" ]; then
    print_device_hints_and_exit
fi

# Safety check 1: refuse known system disk names
case "$DEVICE" in
/dev/sda | /dev/sda[0-9]* | /dev/disk0 | /dev/rdisk0)
    echo "🚫 Refusing to write to known system disk: $DEVICE"
    exit 1
    ;;
esac

# Safety check 2: match against root disk device
ROOT_DEVICE=$(df / | awk 'NR==2 {print $1}' | sed 's/[0-9]*$//')
if [ "$DEVICE" = "$ROOT_DEVICE" ]; then
    echo "🚫 Device appears to be the root filesystem: $DEVICE"
    exit 1
fi

# Find project directories
SENSOS_DIR="$(cd "$(dirname "$0")/../sensos" && pwd)"
PI_GEN_DIR="${SENSOS_DIR}/../pi-gen"
DEPLOY_DIR="${PI_GEN_DIR}/deploy"

cd "$DEPLOY_DIR"

# Step 1: List .img files
echo "📂 Available images:"
img_files=()
i=0
for f in *.img; do
    [ -e "$f" ] || continue
    img_files[$i]="$f"
    i=$((i + 1))
done

if [ ${#img_files[@]} -eq 0 ]; then
    echo "❌ No .img files found in $DEPLOY_DIR"
    exit 1
fi

if [ ${#img_files[@]} -eq 1 ]; then
    IMAGE="${img_files[0]}"
    echo "✅ One image found: $IMAGE"
else
    for i in "${!img_files[@]}"; do
        printf "%2d: %s\n" "$i" "${img_files[$i]}"
    done
    echo ""
    printf "❓ Enter the number of the image to flash: "
    read index
    case "$index" in
    '' | *[!0-9]*)
        echo "❌ Invalid selection."
        exit 1
        ;;
    *) if [ "$index" -lt 0 ] || [ "$index" -ge "${#img_files[@]}" ]; then
        echo "❌ Invalid selection."
        exit 1
    fi ;;
    esac
    IMAGE="${img_files[$index]}"
    echo "✅ Selected: $IMAGE"
fi

# Step 2: Final confirmations
echo ""
printf "⚠️  This will write to $DEVICE and erase its contents. Proceed? [y/N] "
read confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "❌ Aborted."
    exit 1
fi

# Step 3: Unmount, write, eject
echo "📤 Unmounting $DEVICE..."
if command -v diskutil >/dev/null 2>&1; then
    diskutil unmountDisk "$DEVICE"
else
    sudo umount "${DEVICE}"* || true
fi

echo "📝 Writing $IMAGE to $DEVICE..."
sudo dd if="$IMAGE" of="$DEVICE" bs=4M status=progress conv=sync

echo "💿 Ejecting $DEVICE..."
if command -v diskutil >/dev/null 2>&1; then
    diskutil eject "$DEVICE"
else
    sudo eject "$DEVICE" || true
fi

echo "✅ Done!"
