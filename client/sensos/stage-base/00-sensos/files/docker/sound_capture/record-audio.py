import logging
import sounddevice as sd
import numpy as np
import psycopg
import librosa
import queue
import time
import threading
import datetime
import os
import sys

# Configure logging.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Database connection parameters
DB_PARAMS = f"dbname={os.environ.get('POSTGRES_DB', 'postgres')} user={os.environ.get('POSTGRES_USER', 'postgres')} password={os.environ.get('POSTGRES_PASSWORD', 'sensos')} host={os.environ.get('DB_HOST', 'sensos-client-database')} port={os.environ.get('DB_PORT', '5432')}"

# Audio settings
SAMPLE_RATE = 48000
CHANNELS = 1
BIT_DEPTH = 16
SEGMENT_DURATION = 3
STEP_SIZE = 1
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION
STEP_SIZE_FRAMES = SAMPLE_RATE * STEP_SIZE
AUDIO_FORMAT = np.int16

AUDIO_SOURCE = os.environ.get("AUDIO_SOURCE", "record").lower()
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", None)

try:
    RECORD_DURATION = int(os.environ.get("RECORD_DURATION", "0"))
except ValueError:
    RECORD_DURATION = 0

buffer = np.zeros(SEGMENT_SIZE, dtype=AUDIO_FORMAT)
buffer_lock = threading.Lock()
audio_queue = queue.Queue(maxsize=50)
latest_input_time = None
latest_input_frames = 0


def get_device_by_name(name):
    devices = sd.query_devices()
    if name.startswith("hw:"):
        return name
    for idx, dev in enumerate(devices):
        if name.lower() in dev["name"].lower():
            return idx
    return None


def connect_with_retry(max_attempts=10, delay=5):
    for attempt in range(max_attempts):
        try:
            conn = psycopg.connect(DB_PARAMS)
            logging.info("Connected to database!")
            return conn
        except psycopg.OperationalError as e:
            logging.warning(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(delay)
    raise Exception("Failed to connect to database after multiple attempts")


conn = connect_with_retry()
cursor = conn.cursor()


def initialize_schema():
    cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    cursor.execute("CREATE SCHEMA IF NOT EXISTS sensos;")
    cursor.execute(
        f"ALTER DATABASE {os.environ.get('POSTGRES_DB', 'postgres')} SET search_path TO sensos, public;"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.recording_sessions (
            id SERIAL PRIMARY KEY,
            start_time TIMESTAMPTZ NOT NULL,
            sample_rate INT NOT NULL,
            bit_depth INT NOT NULL,
            channel_count INT NOT NULL,
            device TEXT
        );
        CREATE TABLE IF NOT EXISTS sensos.recordings (
            id SERIAL PRIMARY KEY,
            session_id INTEGER REFERENCES sensos.recording_sessions(id) ON DELETE CASCADE,
            t_begin TIMESTAMPTZ NOT NULL,
            t_end TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sensos.raw_audio (
            segment_id INTEGER PRIMARY KEY REFERENCES sensos.recordings(id) ON DELETE CASCADE,
            data BYTEA NOT NULL
        );
    """
    )
    conn.commit()
    logging.info("Database schema initialized.")


initialize_schema()


def start_new_session():
    cursor.execute(
        """
        INSERT INTO sensos.recording_sessions (start_time, sample_rate, bit_depth, channel_count, device)
        VALUES (NOW(), %s, %s, %s, %s)
        RETURNING id;
    """,
        (SAMPLE_RATE, BIT_DEPTH, CHANNELS, AUDIO_DEVICE),
    )
    session_id = cursor.fetchone()[0]
    conn.commit()
    return session_id


session_id = start_new_session()
logging.info(f"Started recording session {session_id} using mode: {AUDIO_SOURCE}")


def store_audio(segment, start, end, session_id):
    cursor.execute(
        """
        INSERT INTO sensos.recordings (session_id, t_begin, t_end)
        VALUES (%s, %s, %s) RETURNING id;
    """,
        (session_id, start, end),
    )
    segment_id = cursor.fetchone()[0]

    cursor.execute(
        "INSERT INTO sensos.raw_audio (segment_id, data) VALUES (%s, %s);",
        (segment_id, psycopg.Binary(segment.tobytes())),
    )
    conn.commit()
    logging.info(f"Stored segment {segment_id} from {start} to {end}.")


def audio_consumer():
    while True:
        item = audio_queue.get()
        if item is None:
            break
        store_audio(*item, session_id)


db_thread = threading.Thread(target=audio_consumer, daemon=True)
db_thread.start()


def callback(indata, frames, time_info, status):
    global buffer, latest_input_time, latest_input_frames
    if status:
        logging.warning(status)
    with buffer_lock:
        buffer[:-frames] = buffer[frames:]
        buffer[-frames:] = indata[:, 0]
        latest_input_time = time_info["input_buffer_adc_time"]
        latest_input_frames = frames


def enqueue_segments():
    global buffer, latest_input_time, latest_input_frames
    while True:
        time.sleep(STEP_SIZE)
        with buffer_lock:
            segment_copy = buffer.copy()
            if latest_input_time:
                end_timestamp = datetime.datetime.utcfromtimestamp(
                    latest_input_time + latest_input_frames / SAMPLE_RATE
                )
            else:
                end_timestamp = datetime.datetime.utcnow()
        start_timestamp = end_timestamp - datetime.timedelta(seconds=SEGMENT_DURATION)
        try:
            audio_queue.put((segment_copy, start_timestamp, end_timestamp), block=False)
        except queue.Full:
            logging.warning("Queue full: dropping segment")


def cleanup():
    logging.info("Shutting down... Flushing remaining audio data.")
    audio_queue.put(None)
    db_thread.join()
    cursor.close()
    conn.close()
    logging.info("Database connection closed. Exiting.")
    sys.exit(0)


def run_recording():
    enqueue_thread = threading.Thread(target=enqueue_segments, daemon=True)
    enqueue_thread.start()

    device = AUDIO_DEVICE
    if device and not device.isdigit():
        device = get_device_by_name(device)

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=AUDIO_FORMAT,
            device=device,
            callback=callback,
        ):
            logging.info("Recording... Press Ctrl+C to stop.")
            start_time = time.time()
            while RECORD_DURATION <= 0 or time.time() - start_time < RECORD_DURATION:
                time.sleep(1)
    except KeyboardInterrupt:
        pass

    cleanup()


if __name__ == "__main__":
    run_recording()
