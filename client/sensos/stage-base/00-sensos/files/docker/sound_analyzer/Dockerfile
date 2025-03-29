FROM debian:bookworm-slim

ENV REQUIRED_DEBS="python3 python3-venv python3-pip libportaudio2 libpq5 libsndfile1 libopenblas0 ffmpeg"
RUN /lib/install-debs.sh

WORKDIR /app

# Set up virtual environment
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"

# Install Python dependencies from pre-downloaded wheels (fallback to PyPI if needed)
RUN pip install --find-links=/wheels/sound_analyzer --prefer-binary -r requirements.txt

# Skip COPY — assumes /app, /wheels, and /debs are mounted
CMD ["python", "analyze-sound.py"]
