FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    wireguard-tools \
    iputils-ping \
    iptables \
    iproute2 \
    procps \
    nginx \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

COPY proxy.conf /etc/nginx/conf.d/default.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
