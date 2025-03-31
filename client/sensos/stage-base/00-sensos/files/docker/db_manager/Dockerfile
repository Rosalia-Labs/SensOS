FROM debian:bookworm-slim

ARG HAS_CONNECTIVITY=false
ENV HAS_CONNECTIVITY=$HAS_CONNECTIVITY

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="python3 python3-venv python3-pip libpq5 libsndfile1"

# Conditionally install packages
COPY ./os_packages/ /os_packages/
RUN set -ex; \
    if [ "$HAS_CONNECTIVITY" = "true" ]; then \
    echo "🌐 HAS_CONNECTIVITY=true: Installing packages from the internet"; \
    apt-get update && apt-get install -y --no-install-recommends $REQUIRED_DEBS && rm -rf /var/lib/apt/lists/*; \
    else \
    echo "🚫 HAS_CONNECTIVITY=false: Installing packages from local DEBs"; \
    if [ -d /os_packages ]; then \
    dpkg -i /os_packages/*.deb || true; \
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

# Set up virtual environment
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

# Conditionally install Python dependencies
COPY requirements.txt .
COPY ./wheels/ /wheels/
RUN if [ "$HAS_CONNECTIVITY" = "true" ]; then \
    echo "🌐 Installing Python dependencies from PyPI"; \
    pip install -r requirements.txt; \
    else \
    echo "🚫 Installing Python dependencies from local wheels"; \
    pip install --find-links=/wheels --prefer-binary -r requirements.txt; \
    fi

# Add main script
COPY manage-db.py .
CMD ["python", "manage-db.py"]
