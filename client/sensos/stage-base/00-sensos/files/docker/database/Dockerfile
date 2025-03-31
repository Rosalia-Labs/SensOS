FROM postgres:17-bookworm

ARG HAS_CONNECTIVITY=false
ENV HAS_CONNECTIVITY=$HAS_CONNECTIVITY

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="postgresql-postgis postgresql-17-pgvector"

COPY ./os_packages/ /os_packages/
RUN set -ex; \
    echo "📦 Attempting offline installation of packages from /os_packages..."; \
    if [ -d /os_packages ] && ls /os_packages/*.deb >/dev/null 2>&1; then \
    dpkg -i /os_packages/*.deb || true; \
    dpkg --configure -a || true; \
    else \
    echo "🪹 No .deb files found in /os_packages or directory missing."; \
    fi; \
    if [ "$OFFLINE" = "false" ]; then \
    echo "🌐 Not in offline mode. Ensuring required packages are installed..."; \
    apt-get update && apt-get install -y --no-install-recommends $REQUIRED_DEBS && rm -rf /var/lib/apt/lists/*; \
    fi

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]