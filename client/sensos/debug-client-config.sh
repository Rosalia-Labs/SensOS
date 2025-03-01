#!/bin/bash
set -e # Exit on error

# Define the Docker image name
DOCKER_IMAGE="debian-sensos"

# Ensure the script exists locally (if needed for later debugging)
LOCAL_SCRIPT_PATH="stage-base/00-sensos/files/config-sensos-client"
if [ ! -f "$LOCAL_SCRIPT_PATH" ]; then
    echo "Error: Script not found locally: $LOCAL_SCRIPT_PATH"
    exit 1
fi

# Get the IP of the `sensos-controller` container
SENSOS_CONTROLLER_IP=$(docker network inspect server_sensos_network -f '{{range .Containers}}{{if eq .Name "sensos-controller"}}{{.IPv4Address}}{{end}}{{end}}' | cut -d'/' -f1)

if [ -z "$SENSOS_CONTROLLER_IP" ]; then
    echo "Error: Could not determine sensos-controller IP."
    exit 1
fi

echo "Using sensos-controller IP: $SENSOS_CONTROLLER_IP"

# Always rebuild the Docker image
echo "Building Docker image..."
docker build -t "$DOCKER_IMAGE" - <<EOF
FROM debian:stable-slim

RUN apt update && apt install -y \
    python3 python3-venv python3-pip \
    bash sudo \
    wireguard-tools openssh-client \
    systemd dbus iproute2 net-tools curl

# Allow 'sensos' to run sudo commands without a password
RUN echo "sensos ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/sensos

# Create the 'sensos' user with a home directory
RUN useradd -m -s /bin/bash sensos

USER sensos
WORKDIR /home/sensos
ENV PATH="/home/sensos/venv/bin:\$PATH"

# Create a virtual environment in the sensos home directory
RUN python3 -m venv /home/sensos/venv && \
    /home/sensos/venv/bin/pip install --upgrade pip && \
    /home/sensos/venv/bin/pip install requests
EOF

docker run --rm -it \
    --privileged \
    --network server_sensos_network \
    -v /sys/fs/cgroup:/sys/fs/cgroup:ro \
    -e SENSOS_CONTROLLER_IP="$SENSOS_CONTROLLER_IP" \
    --mount type=bind,source="$(pwd)/stage-base/00-sensos/files",target=/mnt/config \
    "$DOCKER_IMAGE" bash -c '
    set -e
    echo "Entering interactive bash shell..."
    echo "SENSOS_CONTROLLER_IP is set to: $SENSOS_CONTROLLER_IP"
    exec bash
'
