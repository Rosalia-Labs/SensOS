FROM debian:bookworm-slim

ENV REQUIRED_DEBS="python3 python3-venv python3-pip libpq5 libsndfile1"

RUN /lib/install-debs.sh

WORKDIR /app

# Set up virtual environment
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

# Install Python dependencies from local wheel cache
COPY requirements.txt .
RUN pip install --no-cache-dir --find-links=/wheels/db_manager --prefer-binary -r requirements.txt

# Add main script
COPY manage-db.py .

CMD ["python", "manage-db.py"]
