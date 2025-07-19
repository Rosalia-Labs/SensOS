FROM debian:bookworm-slim

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="python3 python3-venv python3-pip libpq5 libsndfile1"

RUN apt-get update && \
    apt-get install -y --no-install-recommends $REQUIRED_DEBS && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

COPY requirements.txt .

RUN pip install -r requirements.txt

# Add main script
COPY manage_db.py db_utils.py .
CMD ["python", "manage_db.py"]
