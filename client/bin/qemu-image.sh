#!/bin/bash
set -e # Exit on error

IMAGE_PATH="$1"

if [ -z "$IMAGE_PATH" ]; then
    echo "Usage: $0 <path-to-img>"
    exit 1
fi

# Ensure the image path is absolute
IMAGE_PATH="$(cd "$(dirname "$IMAGE_PATH")" && pwd)/$(basename "$IMAGE_PATH")"

echo "Using image: $IMAGE_PATH"

# Build the Docker container with QEMU & guestmount
docker build -t debian-qemu-env - <<EOF
FROM debian:stable-slim
RUN apt update && apt install -y \
    libguestfs-tools zstd qemu-system-aarch64 qemu-utils
EOF

# Find next power of 2 for resizing
if [[ "$(uname)" == "Darwin" ]]; then
    IMAGE_SIZE_BYTES=$(stat -f "%z" "$IMAGE_PATH") # macOS
else
    IMAGE_SIZE_BYTES=$(stat --format="%s" "$IMAGE_PATH") # Linux
fi

IMAGE_SIZE_GB=$((IMAGE_SIZE_BYTES / (1024 * 1024 * 1024)))

NEXT_POWER_OF_2=4
if [ "$IMAGE_SIZE_GB" -gt 4 ]; then
    NEXT_POWER_OF_2=8
    while [ "$NEXT_POWER_OF_2" -lt "$IMAGE_SIZE_GB" ]; do
        NEXT_POWER_OF_2=$((NEXT_POWER_OF_2 * 2))
    done
fi

echo "Resizing temporary image to ${NEXT_POWER_OF_2}GB..."

# Run Docker container to extract & run QEMU
docker run --rm -it --privileged \
    --mount type=bind,source="$IMAGE_PATH",target=/image.img,readonly \
    debian-qemu-env bash -c '
    set -e

    echo "Resizing image..."
    cp /image.img /tmp/image.img
    qemu-img resize -f raw /tmp/image.img '"${NEXT_POWER_OF_2}"'G

    echo "Mounting root partition..."
    mkdir -p /mnt/pi-root
    guestmount --ro -a /tmp/image.img -m /dev/sda2 /mnt/pi-root || { echo "Failed to mount root partition"; exit 1; }

    # Find the v8 kernel for QEMU
    KERNEL_FILE=$(find /mnt/pi-root/boot -type f -name "vmlinuz-*-v8" | sort | tail -n 1)
    INITRD_FILE=$(find /mnt/pi-root/boot -type f -name "initrd.img-*-v8" | sort | tail -n 1)

    if [[ -z "$KERNEL_FILE" || -z "$INITRD_FILE" ]]; then
        echo "Error: Kernel or initrd not found!"
        exit 1
    fi

    echo "Selected Kernel: $KERNEL_FILE"
    echo "Selected Initrd: $INITRD_FILE"

    # Extract the SoC number (only for non-v8 kernels)
    if [[ "$KERNEL_FILE" != *"-v8" ]]; then
        SOC_NAME=$(basename "$KERNEL_FILE" | sed -E "s/.*rpt-rpi-([0-9]+).*/\1/")
    else
        SOC_NAME="qemu-virt"
    fi

   if [ -z "$SOC_NAME" ]; then
        echo "Error: Could not extract SoC number from kernel filename!"
        exit 1
    fi

    echo "Detected SoC: bcm$SOC_NAME"

    # Check if /usr/share/qemu exists and contains relevant DTB files
    QEMU_DTB_DIR="/usr/share/qemu"
    if [ -d "$QEMU_DTB_DIR" ]; then
        QEMU_DTB_FILE=$(find "$QEMU_DTB_DIR" -type f -name "*.dtb" | grep -E "virt|generic" | head -n 1)
    fi

    # If no QEMU DTB is found, attempt to boot without one
    if [ -z "$QEMU_DTB_FILE" ]; then
        echo "Warning: No QEMU DTB found. Attempting to boot without a DTB."
        DTB_FILE=""  # QEMU will try to boot without a DTB
    else
        DTB_FILE="$QEMU_DTB_FILE"
        echo "Using QEMU DTB: $DTB_FILE"
    fi

    echo "Extracting required files..."

    # Copy kernel and initrd
    cp "$KERNEL_FILE" /tmp/vmlinuz
    cp "$INITRD_FILE" /tmp/initrd.img

    # Only copy DTB if it exists
    if [ -n "$DTB_FILE" ]; then
        cp "$DTB_FILE" /tmp/dtb.img
    else
        echo "No DTB file to copy. QEMU will attempt to boot without a DTB."
    fi

    umount /mnt/pi-root

    echo "Files extracted. Starting decompression..."

    # Decompress Kernel
    if file /tmp/vmlinuz | grep -q "gzip compressed data"; then
        echo "Decompressing kernel..."
        zcat </tmp/vmlinuz >/tmp/Image
        rm /tmp/vmlinuz
    else
        mv /tmp/vmlinuz /tmp/Image
    fi

    # Decompress Initrd
    if file /tmp/initrd.img | grep -q "Zstandard compressed data"; then
        echo "Decompressing initrd..."
        unzstd /tmp/initrd.img -o /tmp/initrd.img.decompressed
        mv /tmp/initrd.img.decompressed /tmp/initrd.img
    fi

    # Launch QEMU, only pass -drive if TEMP_IMAGE exists
    if [ -f "$TEMP_IMAGE" ]; then
        QEMU_DRIVE="-drive file=$TEMP_IMAGE,format=raw,if=virtio"
    else
        echo "Warning: No valid disk image. Booting without rootfs."
        QEMU_DRIVE=""
    fi

    # Launch QEMU
    if [ -n "$DTB_FILE" ]; then
        qemu-system-aarch64 \
            -M virt \
            -cpu cortex-a72 \
            -kernel /tmp/Image \
            -initrd /tmp/initrd.img \
            -dtb "$DTB_FILE" \
            -append "console=ttyAMA0 root=/dev/vda2 rootfstype=ext4 rw nokaslr" \
            $QEMU_DRIVE \
            -serial mon:stdio \
            -nographic \
            -no-reboot \
            -d int
    else
        qemu-system-aarch64 \
            -M virt \
            -cpu cortex-a72 \
            -kernel /tmp/Image \
            -initrd /tmp/initrd.img \
            -append "console=ttyAMA0 root=/dev/vda2 rootfstype=ext4 rw nokaslr" \
            $QEMU_DRIVE \
            -serial mon:stdio \
            -nographic \
            -no-reboot \
            -d int
    fi
'

echo "QEMU has exited. Cleanup complete."
