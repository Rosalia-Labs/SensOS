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
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

# Copy and install Python requirements (with cache retained)
COPY requirements.txt .
RUN pip install --no-cache-dir --find-links=/wheels --prefer-binary -r requirements.txt

# Copy your script
COPY manage-db.py .

CMD ["python", "manage-db.py"]
