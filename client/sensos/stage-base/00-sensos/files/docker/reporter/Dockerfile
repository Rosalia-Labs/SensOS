FROM debian:bookworm-slim

WORKDIR /app

ENV REQUIRED_DEBS="python3 python3-venv python3-pip"
RUN apt-get update && \
    apt-get install -y --no-install-recommends $REQUIRED_DEBS && \
    rm -rf /var/lib/apt/lists/*
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY reporter.py .
ENTRYPOINT ["python3", "reporter.py"]
