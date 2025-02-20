FROM debian:bookworm-slim

# Install required packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    iproute2 \
    wireguard \
    ca-certificates \
    python3 \
    python3-venv \
    python3-pip \
    iputils-ping \
    net-tools \
    dnsutils \
    tcpdump \
    telnet \
    netcat-openbsd \
    wget

# Get Debian codename using /etc/os-release instead of lsb_release
RUN install -d /usr/share/postgresql-common/pgdg && \
    curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc && \
    sh -c 'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(grep VERSION_CODENAME /etc/os-release | cut -d= -f2)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

# Set up a virtual environment
WORKDIR /app
RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# Install Python dependencies inside the venv
RUN /app/venv/bin/pip install --upgrade pip && \
    /app/venv/bin/pip install fastapi uvicorn python-multipart psycopg logging docker

# Copy application files
COPY . /app

# Expose API port
EXPOSE 8000

# Run FastAPI with the virtual environment
CMD ["/app/venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
