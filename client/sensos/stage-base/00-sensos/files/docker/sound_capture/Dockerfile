FROM debian:bookworm-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    libpq5 \
    libsndfile1 \
    libopenblas0 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Set up virtual environment
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --find-links=/wheels --prefer-binary -r requirements.txt

# Copy application code
COPY capture-audio.py .

# Default command
CMD ["python", "capture-audio.py"]
