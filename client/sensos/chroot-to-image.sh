#!/bin/bash
set -e # Exit on error

IMAGE_PATH="$1"

if [ -z "$IMAGE_PATH" ]; then
    echo "Usage: $0 <path-to-img>"
    exit 1
fi

# Ensure the image path is absolute
IMAGE_PATH="$(realpath "$IMAGE_PATH")"

echo "Using image: $IMAGE_PATH"

docker build -t debian-guestmount - <<EOF
FROM debian:stable-slim
RUN apt update && apt install -y libguestfs-tools
EOF

docker run --rm -it --privileged \
    --mount type=bind,source="$IMAGE_PATH",target=/image.img \
    debian-guestmount bash -c '
    set -e

    if [ ! -f /image.img ]; then
        echo "Error: Image file not found inside container!"
        exit 1
    fi

    echo "Mounting filesystem using guestmount..."
    mkdir -p /mnt/pi-root
    guestmount --rw -a /image.img -m /dev/sda2 /mnt/pi-root || { echo "Failed to mount root partition"; exit 1; }

    echo "Entering chroot environment..."
    mount --bind /dev /mnt/pi-root/dev
    mount --bind /proc /mnt/pi-root/proc
    mount --bind /sys /mnt/pi-root/sys
    mount --bind /run /mnt/pi-root/run

    chroot /mnt/pi-root /bin/bash

    echo "Exiting chroot. Cleaning up..."
    umount /mnt/pi-root/dev
    umount /mnt/pi-root/proc
    umount /mnt/pi-root/sys
    umount /mnt/pi-root/run
    umount /mnt/pi-root
  '

echo
read -p "Do you want to remove the 'debian-guestmount' Docker image? (y/N) " CONFIRM_REMOVE
if [[ "$CONFIRM_REMOVE" =~ ^[Yy]$ ]]; then
    docker rmi debian-guestmount
    echo "Docker image 'debian-guestmount' removed."
else
    echo "Docker image retained."
fi
