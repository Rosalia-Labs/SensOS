FROM debian:bookworm-slim

WORKDIR /app

# Set required DEBs
ENV REQUIRED_DEBS="\
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    i2c-tools \
    libgpiod-dev \
    libffi-dev \
    libpq-dev \
    git \
    "

RUN apt-get update && \
    apt-get install -y --no-install-recommends $REQUIRED_DEBS && \
    rm -rf /var/lib/apt/lists/*

# Set up virtual environment
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

COPY requirements.txt .

RUN pip install -r requirements.txt

COPY read_i2c.py .
CMD ["python", "read_i2c.py"]
