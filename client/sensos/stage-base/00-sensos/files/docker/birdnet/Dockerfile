FROM debian:bookworm-slim

ENV REQUIRED_DEBS="python3 python3-venv python3-pip libpq5 libsndfile1 libopenblas0 ffmpeg"
RUN /lib/install-debs.sh

WORKDIR /app

# Set up virtual environment
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

# Install Python dependencies (from mounted /wheels and requirements.txt)
RUN pip install --no-index --find-links=/wheels/birdnet --prefer-binary -r requirements.txt

# Default command
CMD ["python", "birdnet-analyze.py"]
