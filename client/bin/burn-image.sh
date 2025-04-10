#!/bin/bash
set -euo pipefail

# Default device (can be overridden with --device)
DEVICE="/dev/rdisk4"

# Parse arguments
while [[ $# -gt 0 ]]; do
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

# Safety check: refuse to write to main system disks
if [[ "$DEVICE" =~ ^/dev/(sd[a]|disk0|rdisk0)$ ]]; then
    echo "🚫 Refusing to write to system disk: $DEVICE"
    exit 1
fi

SENSOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../sensos" && pwd)"
PI_GEN_DIR="${SENSOS_DIR}/../pi-gen"
DEPLOY_DIR="${PI_GEN_DIR}/deploy"

cd "$DEPLOY_DIR"

# Step 1: List .img files
echo "📂 Available images:"
img_files=()
while IFS= read -r f; do
    img_files+=("$f")
done < <(ls *.img 2>/dev/null || true)

if [[ ${#img_files[@]} -eq 0 ]]; then
    echo "❌ No .img files found in $DEPLOY_DIR"
    exit 1
fi

for i in "${!img_files[@]}"; do
    printf "%2d: %s\n" "$i" "${img_files[$i]}"
done

# Step 2: Prompt user to choose
echo ""
read -p "❓ Enter the number of the image to flash: " index

if ! [[ "$index" =~ ^[0-9]+$ ]] || ((index < 0 || index >= ${#img_files[@]})); then
    echo "❌ Invalid selection."
    exit 1
fi

IMAGE="${img_files[$index]}"
echo "✅ Selected: $IMAGE"

# Step 3: Final confirmation
echo ""
read -p "⚠️  This will write to $DEVICE and erase its contents. Proceed? [y/N] " confirm
if [[ "$confirm" != [yY] ]]; then
    echo "❌ Aborted."
    exit 1
fi

# Step 4: Unmount, write, eject
echo "📤 Unmounting $DEVICE..."
diskutil unmountDisk "$DEVICE"

echo "📝 Writing $IMAGE to $DEVICE..."
sudo dd if="$IMAGE" of="$DEVICE" bs=4m status=progress conv=sync

echo "💿 Ejecting $DEVICE..."
diskutil eject "$DEVICE"

echo "✅ Done!"
