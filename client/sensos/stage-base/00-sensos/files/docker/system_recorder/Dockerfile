FROM debian:bookworm-slim

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="python3 python3-venv python3-pip python3-psycopg"

RUN apt-get update && \
    apt-get install -y --no-install-recommends $REQUIRED_DEBS && \
    rm -rf /var/lib/apt/lists/*

COPY system-recorder.py .
CMD ["python3", "system-recorder.py"]
