FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    libportaudio2 \
    portaudio19-dev \
    alsa-utils \
    libpq5 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

RUN pip install sounddevice numpy psycopg librosa

COPY record-audio.py .

CMD ["python", "record-audio.py"]
