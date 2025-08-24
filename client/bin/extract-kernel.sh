#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

set -e # Exit on error

IMAGE_PATH="$1"

if [ -z "$IMAGE_PATH" ]; then
    echo "Usage: $0 <path-to-img>"
    exit 1
fi

# Ensure the image path is absolute (portable method)
IMAGE_PATH="$(cd "$(dirname "$IMAGE_PATH")" && pwd)/$(basename "$IMAGE_PATH")"

echo "Using image: $IMAGE_PATH"

# Build the Debian guestmount image if not already available
docker build -t debian-guestmount - <<EOF
FROM debian:stable-slim
RUN apt update && apt install -y libguestfs-tools zstd
EOF

# Run a container to extract files
docker run --rm -i --privileged \
    --mount type=bind,source="$IMAGE_PATH",target=/image.img,readonly \
    --mount type=bind,source="$(pwd)",target=/extracted \
    debian-guestmount bash -c '
    set -e

    echo "Mounting root partition using guestmount..."
    mkdir -p /mnt/pi-root
    guestmount --ro -a /image.img -m /dev/sda2 /mnt/pi-root || { echo "Failed to mount root partition"; exit 1; }

    echo "Finding required files..."

    # Find all available kernels
    KERNEL_FILES=($(find /mnt/pi-root/boot -type f -name "vmlinuz-*"))
    
    # Select the first non-v8 kernel (model-specific)
    KERNEL_FILE=""
    for K in "${KERNEL_FILES[@]}"; do
        if [[ "$K" != *"-v8" ]]; then
            KERNEL_FILE="$K"
            break
        fi
    done

    # If no non-v8 kernel exists, fall back to the generic -v8 kernel
    if [ -z "$KERNEL_FILE" ]; then
        KERNEL_FILE="${KERNEL_FILES[0]}"
    fi

    # Find initrd matching the selected kernel
    INITRD_FILE=$(echo "$KERNEL_FILE" | sed "s/vmlinuz/initrd.img/")

    if [[ -z "$KERNEL_FILE" || -z "$INITRD_FILE" ]]; then
        echo "Error: Kernel or initrd not found!"
        exit 1
    fi

    echo "Selected Kernel: $KERNEL_FILE"
    echo "Selected Initrd: $INITRD_FILE"

    # Extract only the numeric part of the SoC (e.g., 2712)
    SOC_NAME=$(basename "$KERNEL_FILE" | grep -oP "rpt-rpi-\K[0-9]+" | head -n 1)

    if [ -z "$SOC_NAME" ]; then
        echo "Error: Could not extract SoC number from kernel filename!"
        exit 1
    fi

    echo "Detected SoC: bcm$SOC_NAME"

    # Find the correct DTB file for the detected SoC
    DTB_FILE=$(find /mnt/pi-root/usr/lib/linux-image-*/broadcom/ -type f -name "bcm${SOC_NAME}-*.dtb" | sort | tail -n 1)

    if [ -z "$DTB_FILE" ]; then
        echo "Error: No matching DTB file found for SoC: bcm${SOC_NAME}"
        exit 1
    fi

    echo "DTB: $DTB_FILE"

    echo "Extracting required files..."
    cp "$KERNEL_FILE" /extracted/vmlinuz
    cp "$INITRD_FILE" /extracted/initrd.img
    cp "$DTB_FILE" /extracted/dtb.img

    echo "Unmounting root partition..."
    umount /mnt/pi-root
  '

echo "Files extracted. Starting decompression..."

# Decompress the Kernel
if [ -f "vmlinuz" ]; then
    if file vmlinuz | grep -q "gzip compressed data"; then
        echo "Decompressing kernel..."
        zcat <vmlinuz >Image
        file Image
        rm vmlinuz # Remove the compressed kernel
    else
        echo "Kernel is already uncompressed."
        mv vmlinuz Image
    fi
fi

# Decompress the Initrd
if [ -f "initrd.img" ]; then
    if file initrd.img | grep -q "Zstandard compressed data"; then
        echo "Decompressing initrd..."
        unzstd initrd.img -o initrd.img.decompressed
        file initrd.img.decompressed
        mv initrd.img.decompressed initrd.img
    fi
fi

echo "Cleanup complete. Final files:"
ls -lah Image initrd.img dtb.img

echo "Required files are ready for QEMU."

# Prompt for cleanup
echo
read -p "Do you want to remove the 'debian-guestmount' Docker image? (y/N) " CONFIRM_REMOVE
if [[ "$CONFIRM_REMOVE" =~ ^[Yy]$ ]]; then
    docker rmi debian-guestmount
    echo "Docker image 'debian-guestmount' removed."
else
    echo "Docker image retained."
fi
