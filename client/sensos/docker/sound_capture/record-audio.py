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

# Determine the audio source: "record" for live capture or "random" for test signal.
AUDIO_SOURCE = os.environ.get("AUDIO_SOURCE", "record").lower()

# Get the recording duration (in seconds) from environment variables.
# If set to a positive number, recording will stop after that many seconds.
# If not set or set to 0, recording continues indefinitely.
try:
    RECORD_DURATION = int(os.environ.get("RECORD_DURATION", "0"))
except ValueError:
    RECORD_DURATION = 0

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
    # Create table for recording sessions.
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
    # Create table for audio chunks, using bytea for the audio data.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS audio_chunks (
            id SERIAL PRIMARY KEY,
            session_id INTEGER REFERENCES recording_sessions(id) ON DELETE CASCADE,
            timestamp TIMESTAMPTZ NOT NULL,
            end_timestamp TIMESTAMPTZ NOT NULL,
            peak_amplitude FLOAT,
            rms FLOAT,
            snr FLOAT,
            data bytea NOT NULL
        );
        """
    )
    conn.commit()
    print("Database schema initialized.")


# Initialize the schema at startup.
initialize_schema()


def start_new_session():
    """Creates a new recording session in the database and returns session ID."""
    cursor.execute(
        """
        INSERT INTO recording_sessions (start_time, sample_rate, bit_depth, channel_count, device)
        VALUES (NOW(), %s, %s, %s, %s) RETURNING id;
        """,
        (
            SAMPLE_RATE,
            BIT_DEPTH,
            CHANNELS,
            (
                "Raspberry Pi Built-in Mic"
                if AUDIO_SOURCE == "record"
                else "Test Random Signal"
            ),
        ),
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
    """Stores a 3-second audio chunk with start and end timestamps using bytea for data."""
    peak_amplitude, rms, snr = compute_audio_features(audio_data)
    # Convert the numpy array to bytes for storage.
    audio_bytes = audio_data.tobytes()
    cursor.execute(
        """
        INSERT INTO audio_chunks (session_id, timestamp, end_timestamp, data, peak_amplitude, rms, snr)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            session_id,
            start_timestamp,
            end_timestamp,
            psycopg.Binary(audio_bytes),
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
            break  # Stop thread when None is received.
        audio_data, start_timestamp, end_timestamp = item
        store_audio_chunk(audio_data, start_timestamp, end_timestamp, session_id)


# Start a new session at script startup.
session_id = start_new_session()
print(f"Started recording session {session_id} using mode: {AUDIO_SOURCE}")

# Start the background thread.
db_thread = threading.Thread(target=database_worker, daemon=True)
db_thread.start()


def callback(indata, frames, time_info, status):
    """Processes incoming audio in real-time."""
    global buffer
    if status:
        print(status)
    # Shift buffer left and append new data.
    buffer = np.roll(buffer, -STEP_SIZE_FRAMES)
    buffer[-STEP_SIZE_FRAMES:] = indata[:, 0]
    # Store precise timestamps for this chunk.
    start_timestamp = datetime.datetime.utcnow()
    end_timestamp = start_timestamp + datetime.timedelta(seconds=CHUNK_DURATION)
    # Send chunk to the queue (non-blocking).
    audio_queue.put((buffer.copy(), start_timestamp, end_timestamp))


if AUDIO_SOURCE == "record":
    # Live recording mode using sounddevice.
    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=AUDIO_FORMAT,
            callback=callback,
        ):
            print(
                "Recording... Press Ctrl+C to stop (or wait for RECORD_DURATION to elapse)."
            )
            start_time = time.time()
            while True:
                time.sleep(1)  # Keep script running.
                if RECORD_DURATION > 0 and time.time() - start_time >= RECORD_DURATION:
                    print(
                        f"Record duration of {RECORD_DURATION} seconds reached. Stopping recording."
                    )
                    break
    except Exception as e:
        # In record mode, any error is fatal.
        print(f"Audio capture failed with error: {e}")
        raise e

elif AUDIO_SOURCE == "random":
    # Testing mode: generate random audio signal.
    print(
        "Generating random audio signal for testing. Press Ctrl+C to stop (or wait for RECORD_DURATION to elapse)."
    )
    start_time = time.time()
    try:
        while True:
            random_step = np.random.randint(
                -32768, 32767, size=(STEP_SIZE_FRAMES, 1), dtype=AUDIO_FORMAT
            )
            callback(random_step, STEP_SIZE_FRAMES, None, None)
            time.sleep(STEP_SIZE)
            if RECORD_DURATION > 0 and time.time() - start_time >= RECORD_DURATION:
                print(
                    f"Record duration of {RECORD_DURATION} seconds reached. Stopping random signal generation."
                )
                break
    except KeyboardInterrupt:
        print("\nStopping random signal generation...")
else:
    raise ValueError(f"Unknown AUDIO_SOURCE mode: {AUDIO_SOURCE}")

# Cleanup: stop the background thread.
audio_queue.put(None)
db_thread.join()

# Keep the container alive for inspection without generating new data.
print("Recording stopped. Entering idle mode to allow database inspection.")
while True:
    time.sleep(900)

# (Optionally, when you're done inspecting, you could then close the cursor and connection.)
cursor.close()
conn.close()
