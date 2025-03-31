#!/bin/bash
set -euo pipefail

DEVICE="/dev/rdisk4"
SENSOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PI_GEN_DIR="${SENSOS_DIR}/../pi-gen"
DEPLOY_DIR="${PI_GEN_DIR}/deploy"

cd "$DEPLOY_DIR"

# Step 1: List .img files
echo "📂 Available images:"
mapfile -t img_files < <(ls *.img 2>/dev/null || true)

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
sudo dd if="$IMAGE" of="$DEVICE" bs=1G status=progress conv=sync

echo "💿 Ejecting $DEVICE..."
diskutil eject "$DEVICE"

echo "✅ Done!"
