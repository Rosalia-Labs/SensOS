FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip bash sudo \
    wireguard-tools openssh-client nano curl procps \
    iproute2 iptables docker.io iputils-ping 

# Install necessary system dependencies for Docker installation
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    python3

# Create directory for Docker's GPG key and add it
RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc

# Add Docker repository
RUN echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
    https://download.docker.com/linux/debian $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
    >/etc/apt/sources.list.d/docker.list

# Update package lists and install Docker components and Compose plugin
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin

# Optionally, remove any conflicting older docker packages if present
RUN apt-get purge -y docker.io && rm -rf /var/lib/apt/lists/*

# Continue with the rest of your Dockerfile setup

# Create a non-root user 'sensos' with sudo privileges
RUN apt-get update && apt-get install -y --no-install-recommends sudo && \
    useradd -m -s /bin/bash sensos && \
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

# Create and activate a Python virtual environment and install Python dependencies
RUN python3 -m venv /home/sensos/venv && \
    /home/sensos/venv/bin/pip install --upgrade pip && \
    /home/sensos/venv/bin/pip install requests

# Add and extract the tar archive, then fix file ownership
ADD sensos-files.tar.gz /home/sensos/configs/
RUN sudo chown -R sensos:sensos /home/sensos/configs/

# Start Docker in daemon mode and open a bash shell
CMD ["bash"]
