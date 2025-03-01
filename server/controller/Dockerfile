FROM debian:bookworm-slim

# Install required packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl iproute2 wireguard ca-certificates openssh-client \
    python3 python3-venv python3-pip iputils-ping net-tools \
    dnsutils iptables tcpdump telnet procps netcat-openbsd wget

# Add PostgreSQL client repository
RUN install -d /usr/share/postgresql-common/pgdg && \
    curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc && \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
    https://apt.postgresql.org/pub/repos/apt $(grep VERSION_CODENAME /etc/os-release | cut -d= -f2)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client-17 && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --set iptables /usr/sbin/iptables-legacy; \
    update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy

# Create unprivileged user
RUN useradd -m -s /bin/bash sensos

# Switch to user
USER sensos
WORKDIR /home/sensos

# Generate SSH key for the controller
RUN mkdir -p ~/.ssh && chmod 700 ~/.ssh && \
    ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 && \
    chmod 600 ~/.ssh/id_ed25519 && chmod 644 ~/.ssh/id_ed25519.pub && \
    chown -R sensos:sensos ~/.ssh

# Switch to root for installation
USER root
WORKDIR /app

# Set up a virtual environment
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

# Install Python dependencies inside venv
RUN pip install --upgrade pip && \
    pip install fastapi uvicorn python-multipart psycopg logging docker

# Pass build arguments for versioning (must be passed via `--build-arg`)
ARG VERSION_MAJOR
ARG VERSION_MINOR
ARG VERSION_PATCH
ARG VERSION_SUFFIX
ARG GIT_COMMIT
ARG GIT_BRANCH
ARG GIT_TAG
ARG GIT_DIRTY

# Ensure environment variables are correctly set inside the container
ENV VERSION_MAJOR=${VERSION_MAJOR:-"unknown"}
ENV VERSION_MINOR=${VERSION_MINOR:-"unknown"}
ENV VERSION_PATCH=${VERSION_PATCH:-"unknown"}
ENV VERSION_SUFFIX=${VERSION_SUFFIX:-""}
ENV GIT_COMMIT=${GIT_COMMIT:-"unknown"}
ENV GIT_BRANCH=${GIT_BRANCH:-"unknown"}
ENV GIT_TAG=${GIT_TAG:-"unknown"}
ENV GIT_DIRTY=${GIT_DIRTY:-"false"}

# Copy application files with ownership set
COPY . ./

# Log versioning information on startup
CMD echo "🚀 Running Sensos Controller - Version: ${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}-${VERSION_SUFFIX}" && \
    echo "🔍 Git Commit: ${GIT_COMMIT}, Branch: ${GIT_BRANCH}, Tag: ${GIT_TAG}, Dirty: ${GIT_DIRTY}" && \
    uvicorn main:app --host 0.0.0.0 --port 8000
