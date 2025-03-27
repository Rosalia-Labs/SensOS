# Use a slim Debian base image
FROM debian:bookworm-slim

# Install system dependencies in a single step
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    libportaudio2 \
    libpq5 \
    libsndfile1 \
    libopenblas0 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set up a virtual environment
WORKDIR /app
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

# Upgrade pip and install required Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir --find-links=/wheels --prefer-binary -r requirements.txt

# Copy analysis script
COPY analyze-sound.py .

# Set default command
CMD ["python", "analyze-sound.py"]
