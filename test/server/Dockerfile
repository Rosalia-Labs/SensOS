# test/server/Dockerfile
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive

# Tools + Docker CLI (compose v2 plugin)
RUN set -eux; \
  apt-get update; \
  apt-get install -y --no-install-recommends \
    ca-certificates curl bash git gnupg; \
  rm -rf /var/lib/apt/lists/*; \
  install -d -m 0755 /etc/apt/keyrings; \
  curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
  chmod a+r /etc/apt/keyrings/docker.gpg; \
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" \
    > /etc/apt/sources.list.d/docker.list; \
  apt-get update; \
  apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin docker-buildx-plugin; \
  rm -rf /var/lib/apt/lists/*

# Bundle your server repo subtree
WORKDIR /srv/sensos-server
COPY server/ ./   
RUN chmod +x bin/*.sh || true

# Default: just drop into a shell (Compose will override command)
CMD ["/bin/bash"]
