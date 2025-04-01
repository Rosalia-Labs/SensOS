FROM debian:bookworm-slim

ARG NO_LOCAL=false
ENV NO_LOCAL=$NO_LOCAL

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="python3 python3-venv python3-pip libpq5 libsndfile1"

# Add offline .deb files if available
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

# Set up virtual environment
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

COPY requirements.txt .

# Conditionally copy wheels if local allowed
COPY wheels/ /wheels/

RUN set -ex; \
    echo "📦 Installing Python dependencies..."; \
    if [ "$NO_LOCAL" = "false" ]; then \
    echo "📦 Trying offline wheel install from /wheels..."; \
    pip install --no-index --find-links=/wheels --prefer-binary -r requirements.txt || true; \
    fi; \
    echo "🌐 Installing any remaining Python dependencies from PyPI..."; \
    pip install -r requirements.txt

# Add main script
COPY manage-db.py .
CMD ["python", "manage-db.py"]
