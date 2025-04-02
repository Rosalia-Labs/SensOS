FROM debian:bookworm-slim

ARG NO_LOCAL=false
ENV NO_LOCAL=$NO_LOCAL

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="python3 python3-venv python3-pip python3-psycopg"

RUN set -ex; \
    if [ "$NO_LOCAL" = "false" ] && [ -f /os_packages/Packages.gz ]; then \
    echo "📦 Detected local APT repo in /os_packages, configuring..."; \
    echo "deb [trusted=yes] file:/os_packages ./" > /etc/apt/sources.list.d/offline.list; \
    fi; \
    echo "📦 Updating APT sources..."; \
    apt-get update || true; \
    echo "📦 Attempting standard package install..."; \
    apt-get install -y --no-install-recommends $REQUIRED_DEBS || { \
    echo '🧹 Primary install failed, attempting unjam sequence...'; \
    dpkg --configure -a || true; \
    apt-get install -f -y || true; \
    apt-get -y --fix-broken install || true; \
    apt-get install -y --no-install-recommends $REQUIRED_DEBS || true; \
    }; \
    rm -rf /var/lib/apt/lists/*


COPY system-recorder.py .
CMD ["python3", "system-recorder.py"]
