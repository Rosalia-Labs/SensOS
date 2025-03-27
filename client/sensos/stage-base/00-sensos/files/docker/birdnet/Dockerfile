FROM debian:bookworm-slim

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

WORKDIR /app
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --find-links=/wheels --prefer-binary -r requirements.txt

COPY birdnet-analyze.py ./
COPY model/ model/

CMD ["python", "birdnet-analyze.py"]
