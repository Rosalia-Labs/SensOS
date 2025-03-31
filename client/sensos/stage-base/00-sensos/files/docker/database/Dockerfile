FROM postgres:17-bookworm

ARG HAS_CONNECTIVITY=false
ENV HAS_CONNECTIVITY=$HAS_CONNECTIVITY

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="postgresql-postgis postgresql-17-pgvector"

# Conditionally install packages
COPY os_packages/ os_packages/
RUN set -ex; \
    if [ "$HAS_CONNECTIVITY" = "true" ]; then \
    echo "🌐 HAS_CONNECTIVITY=true: Installing packages from the internet"; \
    apt-get update && apt-get install -y --no-install-recommends $REQUIRED_DEBS && rm -rf /var/lib/apt/lists/*; \
    else \
    echo "🚫 HAS_CONNECTIVITY=false: Installing packages from local DEBs"; \
    if [ -d os_packages ]; then \
    dpkg -i os_packages/*.deb || true; \
    dpkg --configure -a || true; \
    else \
    echo "No local DEB directory found."; \
    fi; \
    echo "🔍 Checking for missing packages..."; \
    MISSING=""; \
    for pkg in $REQUIRED_DEBS; do \
    dpkg -s "$pkg" >/dev/null 2>&1 || MISSING="$MISSING $pkg"; \
    done; \
    if [ -n "$MISSING" ]; then \
    echo "🌐 Attempting online install of missing packages: $MISSING"; \
    apt-get update && apt-get install -y --no-install-recommends $MISSING && rm -rf /var/lib/apt/lists/*; \
    else \
    echo "✅ All packages installed from local DEBs"; \
    fi; \
    fi

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]