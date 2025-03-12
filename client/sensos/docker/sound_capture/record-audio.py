import sounddevice as sd
import numpy as np
import psycopg
import queue
import time
import threading
import datetime
import os

# Get connection details from environment variables, with defaults for testing.
db_name = os.environ.get("POSTGRES_DB", "sensos_db")
db_user = os.environ.get("POSTGRES_USER", "postgres")
db_password = os.environ.get("POSTGRES_PASSWORD", "sensos")
db_host = os.environ.get("DB_HOST", "sensos-client-test-database")
db_port = os.environ.get("DB_PORT", "5432")
DB_PARAMS = f"dbname={db_name} user={db_user} password={db_password} host={db_host} port={db_port}"

# Audio settings
SAMPLE_RATE = 48000  # Hz (BirdNET recommended)
CHANNELS = 1  # Mono
BIT_DEPTH = 16  # Bit depth
CHUNK_DURATION = 3  # seconds
STEP_SIZE = 1  # seconds (new chunk every second)
CHUNK_SIZE = SAMPLE_RATE * CHUNK_DURATION  # Frames per chunk
STEP_SIZE_FRAMES = SAMPLE_RATE * STEP_SIZE  # Frames per step
AUDIO_FORMAT = np.int16  # 16-bit PCM

# Circular buffer for overlapping audio storage
buffer = np.zeros(CHUNK_SIZE, dtype=AUDIO_FORMAT)

# Queue for audio chunks
audio_queue = queue.Queue()


def connect_with_retry(max_attempts=10, delay=3):
    """Try to connect to the database, retrying if the connection is refused."""
    for attempt in range(max_attempts):
        try:
            conn = psycopg.connect(DB_PARAMS)
            print("Connected to database!")
            return conn
        except psycopg.OperationalError as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(delay)
    raise Exception("Failed to connect to database after multiple attempts")


# Use the retry function to establish the connection.
conn = connect_with_retry()
cursor = conn.cursor()


def initialize_schema():
    """Initializes the database schema for recording sessions and audio chunks."""
    # Create table for recording sessions
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS recording_sessions (
            id SERIAL PRIMARY KEY,
            start_time TIMESTAMPTZ NOT NULL,
            sample_rate INT NOT NULL,
            bit_depth INT NOT NULL,
            channel_count INT NOT NULL,
            device TEXT
        );
        """
    )
    # Create table for audio chunks
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS audio_chunks (
            id SERIAL PRIMARY KEY,
            session_id INTEGER REFERENCES recording_sessions(id) ON DELETE CASCADE,
            timestamp TIMESTAMPTZ NOT NULL,
            end_timestamp TIMESTAMPTZ NOT NULL,
            data SMALLINT[] NOT NULL,
            peak_amplitude FLOAT,
            rms FLOAT,
            snr FLOAT
        );
        """
    )
    conn.commit()
    print("Database schema initialized.")


# Initialize the schema at startup
initialize_schema()


def start_new_session():
    """Creates a new recording session in the database and returns session ID."""
    cursor.execute(
        """
        INSERT INTO recording_sessions (start_time, sample_rate, bit_depth, channel_count, device)
        VALUES (NOW(), %s, %s, %s, %s) RETURNING id;
        """,
        (SAMPLE_RATE, BIT_DEPTH, CHANNELS, "Raspberry Pi Built-in Mic"),
    )
    session_id = cursor.fetchone()[0]
    conn.commit()
    return session_id


def compute_audio_features(audio_chunk):
    """Compute peak amplitude, RMS, and SNR."""
    peak_amplitude = np.max(np.abs(audio_chunk))
    rms = np.sqrt(np.mean(audio_chunk**2))
    if rms < 1e-12:
        snr = float("inf") if peak_amplitude > 0 else 0.0
    else:
        snr = 20 * np.log10(peak_amplitude / rms)
    return peak_amplitude, rms, snr


def store_audio_chunk(audio_data, start_timestamp, end_timestamp, session_id):
    """Stores a 3-second audio chunk with start and end timestamps."""
    peak_amplitude, rms, snr = compute_audio_features(audio_data)
    cursor.execute(
        """
        INSERT INTO audio_chunks (session_id, timestamp, end_timestamp, data, peak_amplitude, rms, snr)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            session_id,
            start_timestamp,
            end_timestamp,
            audio_data.tolist(),
            peak_amplitude,
            rms,
            snr,
        ),
    )
    conn.commit()
    print(
        f"Stored audio chunk {start_timestamp} - {end_timestamp} (session {session_id})"
    )


def database_worker():
    """Background thread to write audio chunks to PostgreSQL."""
    while True:
        item = audio_queue.get()
        if item is None:
            break  # Stop thread when None is received
        audio_data, start_timestamp, end_timestamp = item
        store_audio_chunk(audio_data, start_timestamp, end_timestamp, session_id)


# Start a new session at script startup
session_id = start_new_session()
print(f"Started recording session {session_id}")

# Start the background thread
db_thread = threading.Thread(target=database_worker, daemon=True)
db_thread.start()


def callback(indata, frames, time, status):
    """Processes incoming audio in real-time."""
    global buffer
    if status:
        print(status)
    # Shift buffer left and append new data
    buffer = np.roll(buffer, -STEP_SIZE_FRAMES)
    buffer[-STEP_SIZE_FRAMES:] = indata[:, 0]
    # Store precise timestamps for this chunk
    start_timestamp = datetime.datetime.utcnow()
    end_timestamp = start_timestamp + datetime.timedelta(seconds=CHUNK_DURATION)
    # Send chunk to the queue (non-blocking)
    audio_queue.put((buffer.copy(), start_timestamp, end_timestamp))


# Attempt to start actual audio capture.
try:
    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=AUDIO_FORMAT, callback=callback
    ):
        print("Recording... Press Ctrl+C to stop.")
        try:
            while True:
                pass  # Keep script running
        except KeyboardInterrupt:
            print("\nStopping recording...")
except Exception as e:
    # If sound capture fails, mock the capture by generating a silent chunk.
    print(f"Audio capture failed with error: {e}")
    print("Falling back to mock capture.")
    fake_audio = np.zeros(CHUNK_SIZE, dtype=AUDIO_FORMAT)
    start_timestamp = datetime.datetime.utcnow()
    end_timestamp = start_timestamp + datetime.timedelta(seconds=CHUNK_DURATION)
    store_audio_chunk(fake_audio, start_timestamp, end_timestamp, session_id)
    print("Sleeping for 5 minutes to allow database inspection...")
    time.sleep(300)
finally:
    # Stop the background thread and close resources.
    audio_queue.put(None)
    db_thread.join()
    cursor.close()
    conn.close()
