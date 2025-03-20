import logging
import sounddevice as sd
import numpy as np
import signal
import sys
import psycopg
import librosa
import queue
import time
import json
import threading
import datetime
import os

# Configure logging.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Get connection details from environment variables, with defaults for testing.
db_name = os.environ.get("POSTGRES_DB", "postgres")
db_user = os.environ.get("POSTGRES_USER", "postgres")
db_password = os.environ.get("POSTGRES_PASSWORD", "sensos")
db_host = os.environ.get("DB_HOST", "sensos-client-database")
db_port = os.environ.get("DB_PORT", "5432")
DB_PARAMS = f"dbname={db_name} user={db_user} password={db_password} host={db_host} port={db_port}"

# Audio settings
SAMPLE_RATE = 48000  # Hz (BirdNET recommended)
CHANNELS = 1  # Mono
BIT_DEPTH = 16  # Bit depth
SEGMENT_DURATION = 3  # seconds
STEP_SIZE = 1  # seconds (new segment every second)
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION  # Frames per segment
STEP_SIZE_FRAMES = SAMPLE_RATE * STEP_SIZE  # Frames per step
AUDIO_FORMAT = np.int16  # 16-bit PCM

# Determine the audio source: "record" for live capture or "random" for test signal.
AUDIO_SOURCE = os.environ.get("AUDIO_SOURCE", "record").lower()
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", None)

# Get the recording duration (in seconds) from environment variables.
try:
    RECORD_DURATION = int(os.environ.get("RECORD_DURATION", "0"))
except ValueError:
    RECORD_DURATION = 0

# Circular buffer for overlapping audio storage
buffer = np.zeros(SEGMENT_SIZE, dtype=AUDIO_FORMAT)

# Queue for audio segments (bounded to prevent memory growth)
audio_queue = queue.Queue(maxsize=50)


def get_device_by_name(name):
    """Finds a device matching the given name or returns the exact ALSA string."""
    devices = sd.query_devices()
    if name.startswith("hw:"):
        return name
    for idx, dev in enumerate(devices):
        if name.lower() in dev["name"].lower():
            return idx
    return None  # Not found


def connect_with_retry(max_attempts=10, delay=3):
    """Try to connect to the database, retrying if the connection is refused."""
    for attempt in range(max_attempts):
        try:
            conn = psycopg.connect(DB_PARAMS)
            logging.info("Connected to database!")
            return conn
        except psycopg.OperationalError as e:
            logging.warning(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(delay)
    raise Exception("Failed to connect to database after multiple attempts")


# Establish the connection.
conn = connect_with_retry()
cursor = conn.cursor()


def initialize_schema():
    """Initializes the database schema for recording sessions and raw audio."""
    cursor.execute(
        """
        CREATE EXTENSION IF NOT EXISTS postgis;
        CREATE SCHEMA IF NOT EXISTS sensos;
        """
    )
    # Create table for recording sessions.
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
        """
    )
    # Create table for audio segments (metadata only).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.recordings (
            id SERIAL PRIMARY KEY,
            session_id INTEGER REFERENCES sensos.recording_sessions(id) ON DELETE CASCADE,
            t_begin TIMESTAMPTZ NOT NULL,
            t_end TIMESTAMPTZ NOT NULL
        );
        """
    )
    # Create table for raw audio storage.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.raw_audio (
            segment_id INTEGER PRIMARY KEY REFERENCES sensos.recordings(id) ON DELETE CASCADE,
            data BYTEA NOT NULL
        );
        """
    )
    conn.commit()
    logging.info("Database schema initialized.")


# Initialize the schema at startup.
initialize_schema()


def start_new_session():
    """Creates a new recording session in the database and returns session ID."""
    cursor.execute(
        """
        INSERT INTO sensos.recording_sessions (
            start_time,
            sample_rate,
            bit_depth,
            channel_count,
            device
        )
        VALUES (
            NOW(),
            %s, %s, %s, %s
        )
        RETURNING id;
        """,
        (
            SAMPLE_RATE,
            BIT_DEPTH,
            CHANNELS,
            AUDIO_DEVICE,
        ),
    )
    session_id = cursor.fetchone()[0]
    conn.commit()
    return session_id


def store_audio(segment, start, end, session_id):
    """Stores a 3-second raw audio segment with metadata."""
    cursor.execute(
        """
        INSERT INTO sensos.recordings (session_id, t_begin, t_end)
        VALUES (%s, %s, %s) RETURNING id;
        """,
        (session_id, start, end),
    )
    segment_id = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO sensos.raw_audio (segment_id, data)
        VALUES (%s, %s);
        """,
        (segment_id, psycopg.Binary(segment.tobytes())),
    )
    conn.commit()
    logging.info(f"Stored segment {segment_id} from {start} to {end}.")


# Start a new session at script startup.
session_id = start_new_session()
logging.info(f"Started recording session {session_id} using mode: {AUDIO_SOURCE}")

# Start the background thread that writes audio segments to the database.
db_thread = threading.Thread(
    target=lambda: [store_audio(*audio_queue.get(), session_id) for _ in iter(int, 1)],
    daemon=True,
)
db_thread.start()


def cleanup():
    """Gracefully shutdown the recording process."""
    logging.info("Shutting down... Flushing remaining audio data.")

    # Stop background thread gracefully
    audio_queue.put(None)
    db_thread.join()

    # Close database connections
    cursor.close()
    conn.close()
    logging.info("Database connection closed. Exiting.")
    sys.exit(0)  # Ensure clean exit


def wait_for_container_stop():
    """Keep container running until stopped, while handling cleanup signals."""
    logging.info("Recording complete. Waiting indefinitely for container shutdown.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()


def callback(indata, frames, time_info, status):
    global buffer
    if status:
        logging.warning(status)
    # Shift buffer left and append new data.
    buffer[: -indata.shape[0]] = buffer[indata.shape[0] :]
    buffer[-indata.shape[0] :] = indata[:, 0]
    # Compute timestamps.
    start_timestamp = datetime.datetime.utcnow()
    end_timestamp = start_timestamp + datetime.timedelta(seconds=SEGMENT_DURATION)
    # Attempt to add to the queue.
    try:
        audio_queue.put((buffer.copy(), start_timestamp, end_timestamp), block=False)
    except queue.Full:
        logging.warning("Queue full: dropping segment")


def run_recording():
    """Handles live recording or test signal generation."""
    start_time = time.time()

    if AUDIO_SOURCE == "record":
        device = AUDIO_DEVICE
        if device and not device.isdigit():
            resolved_device = get_device_by_name(device)
            if resolved_device is not None:
                device = resolved_device
            else:
                logging.warning(f"Audio device '{device}' not found. Using default.")
                device = None  # Default to system-selected device

        logging.info(
            f"Using audio device: {device if device is not None else 'Default'}"
        )

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=AUDIO_FORMAT,
                device=device,
                callback=callback,
            ):
                logging.info(
                    "Recording... Press Ctrl+C to stop (or wait for RECORD_DURATION)."
                )
                while True:
                    time.sleep(1)
                    if (
                        RECORD_DURATION > 0
                        and time.time() - start_time >= RECORD_DURATION
                    ):
                        logging.info(
                            f"RECORD_DURATION {RECORD_DURATION} reached. Waiting indefinitely."
                        )
                        break
        except Exception as e:
            logging.error(f"Audio capture failed: {e}")
            raise

    elif AUDIO_SOURCE == "random":
        logging.info("Generating random test signal. Press Ctrl+C to stop.")
        try:
            while True:
                random_segment = np.random.randint(
                    -32768, 32767, SEGMENT_SIZE, dtype=AUDIO_FORMAT
                )
                start_timestamp = datetime.datetime.utcnow()
                end_timestamp = start_timestamp + datetime.timedelta(
                    seconds=SEGMENT_DURATION
                )
                audio_queue.put((random_segment, start_timestamp, end_timestamp))
                time.sleep(STEP_SIZE)
                if RECORD_DURATION > 0 and time.time() - start_time >= RECORD_DURATION:
                    logging.info(
                        f"RECORD_DURATION {RECORD_DURATION} reached. Waiting indefinitely."
                    )
                    break
        except KeyboardInterrupt:
            logging.info("Stopping test signal generation...")

    elif AUDIO_SOURCE == "file":
        try:
            audio_data, sr = librosa.load(
                "test.wav", sr=SAMPLE_RATE, mono=True, dtype=np.float32
            )
            if sr != SAMPLE_RATE:
                logging.warning(f"Resampling from {sr} Hz to {SAMPLE_RATE} Hz")
                audio_data = librosa.resample(
                    audio_data, orig_sr=sr, target_sr=SAMPLE_RATE
                )
            # Convert float32 to 16-bit PCM
            audio_data = (audio_data * 32767).astype(AUDIO_FORMAT)
            start_idx = 0
            total_frames = len(audio_data)
            start_time = datetime.datetime.utcnow()
            while start_idx + SEGMENT_SIZE <= total_frames:
                segment = audio_data[start_idx : start_idx + SEGMENT_SIZE]
                end_time = start_time + datetime.timedelta(seconds=SEGMENT_DURATION)
                audio_queue.put((segment, start_time, end_time))
                start_idx += STEP_SIZE_FRAMES
                start_time = start_time + datetime.timedelta(seconds=STEP_SIZE)
                if RECORD_DURATION > 0 and (start_idx / SAMPLE_RATE) >= RECORD_DURATION:
                    logging.info(
                        f"RECORD_DURATION {RECORD_DURATION} reached. Stopping file playback."
                    )
                    break
            logging.info("Finished reading file. Waiting indefinitely.")
            wait_for_container_stop()
        except Exception as e:
            logging.error(f"Error reading audio file: {e}")
            raise

    else:
        raise ValueError(f"Unknown AUDIO_SOURCE: {AUDIO_SOURCE}")

    wait_for_container_stop()


# Start recording process.
run_recording()
