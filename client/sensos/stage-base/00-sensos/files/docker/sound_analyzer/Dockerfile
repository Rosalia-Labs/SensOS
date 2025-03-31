FROM debian:bookworm-slim

ARG HAS_CONNECTIVITY=false
ENV HAS_CONNECTIVITY=$HAS_CONNECTIVITY

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="python3 python3-venv python3-pip libportaudio2 libpq5 libsndfile1 libopenblas0 ffmpeg"

# Conditionally install packages
COPY ./os_packages/ /os_packages/
RUN set -ex; \
    echo "📦 Attempting offline installation of packages from /os_packages..."; \
    if [ -d /os_packages ] && ls /os_packages/*.deb >/dev/null 2>&1; then \
    dpkg -i /os_packages/*.deb || true; \
    dpkg --configure -a || true; \
    else \
    echo "🪹 No .deb files found in /os_packages or directory missing."; \
    fi; \
    if [ "$HAS_CONNECTIVITY" = "true" ]; then \
    echo "🌐 HAS_CONNECTIVITY=true: Ensuring required packages are installed..."; \
    apt-get update && apt-get install -y --no-install-recommends $REQUIRED_DEBS && rm -rf /var/lib/apt/lists/*; \
    fi

# Set up virtual environment
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

# Conditionally install Python dependencies
COPY requirements.txt .
COPY ./wheels/ /wheels/
RUN set -ex; \
    echo "📦 Attempting offline install of Python dependencies from /wheels..."; \
    pip install --no-index --find-links=/wheels --prefer-binary -r requirements.txt || true; \
    if [ "$HAS_CONNECTIVITY" = "true" ]; then \
    echo "🌐 HAS_CONNECTIVITY=true: Installing remaining Python dependencies from PyPI"; \
    pip install -r requirements.txt; \
    fi

COPY analyze-sound.py .
CMD ["python", "analyze-sound.py"]