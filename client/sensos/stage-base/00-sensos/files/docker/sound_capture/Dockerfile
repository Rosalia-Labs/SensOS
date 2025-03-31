FROM debian:bookworm-slim

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="python3 python3-venv python3-pip libpq5 libsndfile1 libopenblas0 ffmpeg"

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

# Set up virtual environment
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

# Install Python dependencies (using local wheels if available)
COPY requirements.txt .
COPY wheels/ wheels/
RUN pip install --find-links=wheels/birdnet --prefer-binary -r requirements.txt

COPY capture-audio.py .
CMD ["python", "capture-audio.py"]
