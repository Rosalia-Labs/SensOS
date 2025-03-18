# Use Debian Bookworm Slim as the base image
FROM debian:bookworm-slim

# Install necessary system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip bash sudo \
    wireguard-tools openssh-client nano curl procps \
    iproute2 iptables docker.io iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user 'sensos' with sudo privileges
RUN useradd -m -s /bin/bash sensos && \
    echo "sensos ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/sensos

# Ensure the 'docker' group exists and add 'sensos' to it
RUN groupadd -f docker && usermod -aG docker sensos

# Resolve `host.docker.internal` dynamically and configure insecure registries
RUN echo '#!/bin/bash\n\
    set -e\n\
    LOG_FILE=/var/log/dockerd.log\n\
    mkdir -p /var/log && sudo touch $LOG_FILE && sudo chmod 666 $LOG_FILE\n\
    echo "🚀 Resolving host.docker.internal..." | tee -a $LOG_FILE\n\
    REGISTRY_IP=$(getent ahosts host.docker.internal | awk "!/:/ { print \$1; exit }")\n\
    echo "Resolved registry IP: $REGISTRY_IP" | tee -a $LOG_FILE\n\
    echo "{ \"insecure-registries\": [\"$REGISTRY_IP:5000\"] }" | sudo tee /etc/docker/daemon.json\n\
    echo "🚀 Starting Docker daemon inside container with sudo..." | tee -a $LOG_FILE\n\
    sudo dockerd --data-root=/var/lib/docker \\\n\
    --host=unix:///var/run/docker.sock \\\n\
    --host=tcp://0.0.0.0:2375 \\\n\
    > $LOG_FILE 2>&1 &' > /usr/local/bin/start-docker && chmod +x /usr/local/bin/start-docker

# Switch to the 'sensos' user
USER sensos
WORKDIR /home/sensos

# Set environment variables
ENV PATH="/home/sensos/venv/bin:$PATH"

# Create and activate a Python virtual environment
RUN python3 -m venv /home/sensos/venv && \
    /home/sensos/venv/bin/pip install --upgrade pip && \
    /home/sensos/venv/bin/pip install requests

# Add and extract the tar archive
ADD sensos-files.tar.gz /home/sensos/configs/

# Start Docker in daemon mode and open a bash shell
CMD ["bash"]

