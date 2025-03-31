FROM postgres:17-bookworm

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="postgresql-postgis postgresql-17-pgvector"

# Copy local deb files into the image
COPY os_packages/ os_packages/

# Install all .deb files in os_packages, then install any missing packages via APT
RUN set -ex; \
    echo "📦 Installing all local DEB files..."; \
    if [ -d os_packages ]; then \
    for deb in os_packages/*.deb; do \
    if [ -f "$deb" ]; then \
    echo "Installing $deb"; \
    dpkg -i "$deb" || true; \
    fi; \
    done; \
    else \
    echo "No local DEB directory found."; \
    fi; \
    echo "🔍 Checking for missing packages..."; \
    MISSING=""; \
    for pkg in $REQUIRED_DEBS; do \
    dpkg -s "$pkg" >/dev/null 2>&1 || MISSING="$MISSING $pkg"; \
    done; \
    if [ -n "$MISSING" ]; then \
    echo "🌐 Installing missing packages via APT: $MISSING"; \
    apt-get update && apt-get install -y --no-install-recommends $MISSING && rm -rf /var/lib/apt/lists/*; \
    else \
    echo "✅ All packages installed from local DEBs"; \
    fi

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]