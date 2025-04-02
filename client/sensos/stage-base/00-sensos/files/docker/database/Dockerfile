FROM postgres:17-bookworm

ARG NO_LOCAL=false
ENV NO_LOCAL=$NO_LOCAL

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="postgresql-postgis postgresql-17-pgvector"

COPY os_packages/ /os_packages/

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

COPY postgres.conf /etc/postgresql/postgresql.conf

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]
