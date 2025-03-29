FROM debian:bookworm-slim

ENV REQUIRED_DEBS="python3 python3-venv python3-pip libpq5 libsndfile1 libopenblas0 ffmpeg"
RUN /lib/install-debs.sh

# Setup app environment
WORKDIR /app
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

# Install Python wheels from /wheels
COPY requirements.txt .
RUN pip install --find-links=/wheels/sound_capture --prefer-binary -r requirements.txt

# Add application code
COPY capture-audio.py .

CMD ["python", "capture-audio.py"]
