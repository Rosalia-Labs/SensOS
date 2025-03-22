import threading
import datetime
import logging
import psycopg
import librosa
import shutil
import queue
import time
import json
import sys
import os

import sounddevice as sd
import numpy as np

# Configure logging.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Database connection parameters.
DB_PARAMS = (
    f"dbname={os.environ.get('POSTGRES_DB', 'postgres')} "
    f"user={os.environ.get('POSTGRES_USER', 'postgres')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'sensos')} "
    f"host={os.environ.get('DB_HOST', 'sensos-client-database')} "
    f"port={os.environ.get('DB_PORT', '5432')}"
)

# Audio settings.
SAMPLE_RATE = 48000
try:
    CHANNELS = int(os.environ.get("CHANNELS", "1"))
except ValueError:
    CHANNELS = 1

# Determine the native audio format from the environment.
# Set AUDIO_FORMAT to "float32" or "int16" (default is "float32").
env_audio_format = os.environ.get("AUDIO_FORMAT", "float32").lower()
if env_audio_format == "int16":
    AUDIO_FORMAT = np.int16
elif env_audio_format == "float32":
    AUDIO_FORMAT = np.float32
else:
    logging.error(
        f"Unrecognized AUDIO_FORMAT '{env_audio_format}'. Must be 'float32' or 'int16'. Exiting."
    )
    sys.exit(1)

# BIT_DEPTH is set based on the chosen format.
BIT_DEPTH = 32 if AUDIO_FORMAT == np.float32 else 16

SEGMENT_DURATION = 3  # seconds
STEP_SIZE = 1  # seconds; a smaller value creates overlapping segments
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION
STEP_SIZE_FRAMES = SAMPLE_RATE * STEP_SIZE

# Audio source mode ("record" for live capture, "files" for scanning sound files).
AUDIO_SOURCE = os.environ.get("AUDIO_SOURCE", "record").lower()
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", None)

# Allocate a live-recording buffer using the configured type.
# This buffer is allocated once (for live mode).
buffer = np.zeros((SEGMENT_SIZE, CHANNELS), dtype=AUDIO_FORMAT)
buffer_lock = threading.Lock()
audio_queue = queue.Queue(maxsize=50)


# Database connection.
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
            device TEXT,
            raw_audio_dtype TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sensos.audio_segments (
            id SERIAL PRIMARY KEY,
            session_id INTEGER REFERENCES sensos.recording_sessions(id) ON DELETE CASCADE,
            t_begin TIMESTAMPTZ NOT NULL,
            t_end TIMESTAMPTZ NOT NULL,
            channel INT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sensos.raw_audio (
            segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
            data BYTEA NOT NULL
        );
        -- Table for tracking processed files (for file mode)
        CREATE TABLE IF NOT EXISTS sensos.processed_files (
            id SERIAL PRIMARY KEY,
            file_path TEXT UNIQUE NOT NULL,
            file_mod_time TIMESTAMPTZ,
            processed_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """
    )
    conn.commit()
    logging.info("Database schema initialized.")


initialize_schema()


def start_new_session():
    """
    Start a new recording session and store the raw audio data type.
    This function stores a single column (raw_audio_dtype) that indicates the NumPy data type
    (e.g. "float32" or "int16") of the raw bytes in the database.
    """
    cursor.execute(
        """
        INSERT INTO sensos.recording_sessions 
            (start_time, sample_rate, bit_depth, channel_count, device, raw_audio_dtype)
        VALUES 
            (NOW(), %s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (SAMPLE_RATE, BIT_DEPTH, CHANNELS, AUDIO_DEVICE, env_audio_format),
    )
    session_id = cursor.fetchone()[0]
    conn.commit()
    return session_id


def store_audio(segment, start, end, session_id, channel):
    """
    Store an audio segment in the database.
    If the segment's dtype does not match the configured AUDIO_FORMAT, convert it.
    For example, if AUDIO_FORMAT is float32 and the segment is int16, perform normalization.
    """
    if segment.dtype != AUDIO_FORMAT:
        if AUDIO_FORMAT == np.float32 and segment.dtype == np.int16:
            segment = segment.astype(np.float32) / 32768.0
        else:
            segment = segment.astype(AUDIO_FORMAT)
    cursor.execute(
        """
        INSERT INTO sensos.audio_segments (session_id, t_begin, t_end, channel)
        VALUES (%s, %s, %s, %s) RETURNING id;
        """,
        (session_id, start, end, channel),
    )
    segment_id = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO sensos.raw_audio (segment_id, data) VALUES (%s, %s);",
        (segment_id, psycopg.Binary(segment.tobytes())),
    )
    conn.commit()
    logging.info(
        f"Stored segment {segment_id} for channel {channel} from {start} to {end}."
    )


# === Live recording functions (record mode) ===


def get_device_by_name(name):
    devices = sd.query_devices()
    if name.startswith("hw:"):
        return name
    for idx, dev in enumerate(devices):
        if name.lower() in dev["name"].lower():
            return idx
    return None


def audio_consumer():
    while True:
        item = audio_queue.get()
        if item is None:
            break
        # item is now (segment, start, end, channel)
        store_audio(*item, session_id)


def callback(indata, frames, time_info, status):
    if status:
        logging.warning(status)
    with buffer_lock:
        # Shift the buffer and append new samples.
        buffer[:-frames, :] = buffer[frames:, :]
        buffer[-frames:, :] = indata


def enqueue_segments():
    while True:
        time.sleep(STEP_SIZE)
        with buffer_lock:
            segment_copy = buffer.copy()
        end_timestamp = datetime.datetime.utcnow()
        start_timestamp = end_timestamp - datetime.timedelta(seconds=SEGMENT_DURATION)
        # Enqueue a segment for each channel.
        for ch in range(CHANNELS):
            try:
                audio_queue.put(
                    (segment_copy[:, ch], start_timestamp, end_timestamp, ch),
                    block=False,
                )
            except queue.Full:
                logging.warning(f"Queue full: dropping segment for channel {ch}")


def run_recording():
    global session_id
    # Initialize a new session for live recording.
    session_id = start_new_session()
    db_thread = threading.Thread(target=audio_consumer, daemon=True)
    db_thread.start()
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
            logging.info(
                "Recording... Running indefinitely until container is stopped."
            )
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass

    cleanup(db_thread)


# === File scanning functions (files mode) ===


def process_file(file_path, session_id):
    """
    Load an audio file, ensuring that it is represented as float32 in the range [-1, 1],
    split it into overlapping 3-second segments, and store each channel as a separate segment.
    This function will convert, check, and normalize the audio data if it is not in the expected format.
    """
    logging.info(f"Processing file: {file_path}")
    try:
        # Librosa loads audio with mono=False returns multi-channel data.
        audio, sr = librosa.load(file_path, sr=SAMPLE_RATE, mono=False)
    except Exception as e:
        logging.error(f"Error loading {file_path}: {e}")
        return

    # Verify the data type: Librosa should load as float32.
    if audio.dtype != np.float32:
        logging.warning(
            f"Audio from {file_path} is {audio.dtype} instead of float32; converting to float32."
        )
        # If the data appears to be int16, normalize appropriately.
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        else:
            audio = audio.astype(np.float32)

    # Rescale audio to the range [-1, 1] regardless of its original range.
    min_val = np.min(audio)
    max_val = np.max(audio)
    if max_val != min_val:
        if min_val < -1 or max_val > 1:
            logging.warning(
                f"Audio from {file_path} has range [{min_val}, {max_val}] which is out of [-1, 1]; rescaling."
            )
            audio = 2 * (audio - min_val) / (max_val - min_val) - 1
    else:
        logging.warning(f"Audio from {file_path} has zero range; cannot rescale.")

    # Ensure audio is 2D: shape (channels, samples)
    if audio.ndim == 1:
        audio = np.expand_dims(audio, axis=0)

    total_samples = audio.shape[1]
    if total_samples < SEGMENT_SIZE:
        logging.warning(
            f"File {file_path} is shorter than {SEGMENT_DURATION} seconds, skipping."
        )
        return

    num_channels_in_file = audio.shape[0]
    file_mod_time = datetime.datetime.utcfromtimestamp(os.path.getmtime(file_path))
    step_samples = int(SAMPLE_RATE * STEP_SIZE)
    for start_index in range(0, total_samples - SEGMENT_SIZE + 1, step_samples):
        end_index = start_index + SEGMENT_SIZE
        for ch in range(num_channels_in_file):
            segment = audio[ch, start_index:end_index]
            segment_start = file_mod_time + datetime.timedelta(
                seconds=start_index / SAMPLE_RATE
            )
            segment_end = file_mod_time + datetime.timedelta(
                seconds=end_index / SAMPLE_RATE
            )
            store_audio(segment, segment_start, segment_end, session_id, ch)


def process_directory_mode():
    """
    Continuously scan a directory tree for new sound files that are no longer being written,
    check the processed_files table for persistence, and process new files.
    A new session is initiated for file input mode.
    After processing, files are moved to a 'processed' directory while preserving the original directory structure.
    The processed_files record is updated to reflect the final file path.
    """
    unprocessed_dir = "/mnt/audio_recordings/unprocessed"
    processed_dir = "/mnt/audio_recordings/processed"
    file_stable_threshold = int(os.environ.get("FILE_STABLE_THRESHOLD", "10"))
    logging.info(f"Scanning directory for sound files: {unprocessed_dir}")

    # Initialize a new session for file processing.
    session_id = start_new_session()
    logging.info(f"Started file processing session {session_id}")
    accepted_extensions = (".wav", ".mp3", ".flac", ".ogg")

    while True:
        for root, dirs, files in os.walk(unprocessed_dir):
            for file in files:
                if not file.lower().endswith(accepted_extensions):
                    continue
                file_path = os.path.join(root, file)

                # Check if file has been processed already.
                cursor.execute(
                    "SELECT 1 FROM sensos.processed_files WHERE file_path = %s",
                    (file_path,),
                )
                if cursor.fetchone() is not None:
                    logging.warning(
                        f"Anomaly: File already processed, moving to duplicates: {file_path}"
                    )
                    anomaly_dir = "/mnt/audio_recordings/anomalies"
                    rel_path = os.path.relpath(file_path, unprocessed_dir)
                    destination = os.path.join(anomaly_dir, rel_path)
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    try:
                        shutil.move(file_path, destination)
                        logging.info(
                            f"Moved duplicate file {file_path} to {destination}"
                        )
                    except Exception as e:
                        logging.error(
                            f"Error moving duplicate file {file_path} to {destination}: {e}"
                        )
                    continue

                try:
                    mod_time = os.path.getmtime(file_path)
                except Exception as e:
                    logging.error(
                        f"Could not get modification time for {file_path}: {e}"
                    )
                    sys.exit(1)

                now = time.time()
                if now - mod_time < file_stable_threshold:
                    logging.info(f"Skipping file (recently modified): {file_path}")
                    continue

                # Process the file.
                process_file(file_path, session_id)

                # Insert a record into processed_files with the original path.
                try:
                    cursor.execute(
                        "INSERT INTO sensos.processed_files (file_path, file_mod_time) VALUES (%s, to_timestamp(%s))",
                        (file_path, mod_time),
                    )
                    conn.commit()
                    logging.info(f"Recorded processed file: {file_path}")
                except Exception as e:
                    logging.error(f"Error recording processed file {file_path}: {e}")
                    conn.rollback()

                # Move the processed file to the processed directory, preserving directory structure.
                rel_path = os.path.relpath(file_path, unprocessed_dir)
                destination = os.path.join(processed_dir, rel_path)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                try:
                    shutil.move(file_path, destination)
                    logging.info(f"Moved {file_path} to {destination}")
                except Exception as e:
                    logging.error(f"Error moving {file_path} to {destination}: {e}")
                    continue

                # Update the processed_files record to reflect the new (final) file path.
                try:
                    cursor.execute(
                        "UPDATE sensos.processed_files SET file_path = %s WHERE file_path = %s",
                        (destination, file_path),
                    )
                    conn.commit()
                    logging.info(
                        f"Updated processed file record from {file_path} to {destination}"
                    )
                except Exception as e:
                    logging.error(
                        f"Error updating processed file record for {file_path}: {e}"
                    )
                    conn.rollback()

        time.sleep(5)


# === Cleanup function ===


def cleanup(db_thread=None):
    logging.info("Shutting down... Flushing remaining audio data.")
    audio_queue.put(None)
    if db_thread is not None:
        db_thread.join()
    cursor.close()
    conn.close()
    logging.info("Database connection closed. Exiting.")
    sys.exit(0)


# === Main execution block ===

if __name__ == "__main__":
    if AUDIO_SOURCE == "record":
        logging.info("Starting live recording mode.")
        run_recording()
    elif AUDIO_SOURCE == "files":
        logging.info("Starting file scanning mode.")
        process_directory_mode()
    else:
        logging.error(f"Unknown AUDIO_SOURCE mode: {AUDIO_SOURCE}")
        sys.exit(1)
