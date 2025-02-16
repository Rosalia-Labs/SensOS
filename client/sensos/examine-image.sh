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
    --mount type=bind,source="$IMAGE_PATH",target=/image.img,readonly \
    debian-guestmount bash -c '
    set -e

    echo "Mounting filesystem using guestmount..."
    mkdir -p /mnt/pi-root
    guestmount --ro -a /image.img -m /dev/sda2 /mnt/pi-root || { echo "Failed to mount root partition"; exit 1; }

    echo "Filesystem mounted. You can now examine the system under /mnt/pi-root"
    echo "When finished, exit the shell and the mount will be cleaned up."

    cd /mnt/pi-root
    bash  # Open an interactive shell inside the mounted image
  '

echo
read -p "Do you want to remove the 'debian-guestmount' Docker image? (y/N) " CONFIRM_REMOVE
if [[ "$CONFIRM_REMOVE" =~ ^[Yy]$ ]]; then
    docker rmi debian-guestmount
    echo "Docker image 'debian-guestmount' removed."
else
    echo "Docker image retained."
fi
