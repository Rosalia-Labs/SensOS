import datetime
import logging
import psycopg
import shutil
import time
import sys
import os
import soundfile as sf
import numpy as np

# Configure logging.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Database connection parameters.
# These are defined in the compose file.
DB_PARAMS = (
    f"dbname={os.environ['POSTGRES_DB']} "
    f"user={os.environ['POSTGRES_USER']} "
    f"password={os.environ['POSTGRES_PASSWORD']} "
    f"host={os.environ['DB_HOST']} "
    f"port={os.environ['DB_PORT']}"
)

# Audio settings.
# Hard-coded defaults (file metadata will be determined per file).
SAMPLE_RATE = 48000  # expected sample rate (used for segmentation)
SEGMENT_DURATION = 3  # seconds
STEP_SIZE = 1  # seconds; smaller value creates overlapping segments
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION
STEP_SIZE_FRAMES = SAMPLE_RATE * STEP_SIZE


def extract_timestamp_from_filename(file_path):
    """
    Attempt to extract a timestamp from the filename.
    Expected pattern: sensos_YYYYMMDDTHHMMSS.wav
    Returns the Unix timestamp (float) if successful, or None otherwise.
    """
    base = os.path.basename(file_path)
    if base.startswith("sensos_"):
        timestamp_str = base[len("sensos_") :].split(".")[0]  # e.g., "20250315T123045"
        try:
            dt = datetime.datetime.strptime(timestamp_str, "%Y%m%dT%H%M%S")
            return dt.timestamp()
        except Exception as e:
            logging.error(f"Error parsing timestamp from filename {base}: {e}")
    return None


# --- Database functions ---


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
        f"ALTER DATABASE {os.environ['POSTGRES_DB']} SET search_path TO sensos, public;"
    )
    # The audio_files table now stores the native format as a string.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.audio_files (
            id SERIAL PRIMARY KEY,
            file_path TEXT NOT NULL,
            file_mod_time TIMESTAMPTZ,
            sample_rate INT NOT NULL,
            native_format TEXT NOT NULL,
            channel_count INT NOT NULL,
            processed_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    # Audio segments now reference audio_files.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.audio_segments (
            id SERIAL PRIMARY KEY,
            file_id INTEGER REFERENCES sensos.audio_files(id) ON DELETE CASCADE,
            t_begin TIMESTAMPTZ NOT NULL,
            t_end TIMESTAMPTZ NOT NULL,
            channel INT NOT NULL
        );
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.raw_audio (
            segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
            data BYTEA NOT NULL
        );
        """
    )
    conn.commit()
    logging.info("Database schema initialized.")


initialize_schema()


def create_file_recording(
    file_path, srate, native_format, channel_count, file_mod_time
):
    """
    Create a new audio_files record for the processed file and return its id.
    """
    cursor.execute(
        """
        INSERT INTO sensos.audio_files 
            (file_path, file_mod_time, sample_rate, native_format, channel_count)
        VALUES (%s, to_timestamp(%s), %s, %s, %s)
        RETURNING id;
        """,
        (file_path, file_mod_time, srate, native_format, channel_count),
    )
    file_id = cursor.fetchone()[0]
    conn.commit()
    return file_id


def store_audio(segment, start, end, file_id, channel):
    """
    Store an audio segment in the database, preserving the original sample values.
    """
    cursor.execute(
        """
        INSERT INTO sensos.audio_segments (file_id, t_begin, t_end, channel)
        VALUES (%s, %s, %s, %s) RETURNING id;
        """,
        (file_id, start, end, channel),
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


# --- File scanning functions ---


def process_file(file_path, file_id, metadata):
    """
    Load an audio file and split it into overlapping segments using metadata
    that was already extracted.
    """
    logging.info(f"Processing file: {file_path}")
    try:
        # Use the native format from the metadata
        dtype = metadata["native_format"]
        audio, srate = sf.read(file_path, dtype=dtype)
    except Exception as e:
        logging.error(f"Error loading {file_path}: {e}")
        return

    if srate != SAMPLE_RATE:
        logging.warning(
            f"File sample rate ({srate} Hz) does not match expected {SAMPLE_RATE} Hz."
        )

    # Rearrange the audio data to shape (channels, samples)
    if audio.ndim == 2:
        audio = audio.T
    elif audio.ndim == 1:
        audio = np.expand_dims(audio, axis=0)

    total_samples = audio.shape[1]
    if total_samples < SEGMENT_SIZE:
        logging.warning(
            f"File {file_path} is shorter than {SEGMENT_DURATION} seconds, skipping."
        )
        return

    step_samples = int(SAMPLE_RATE * STEP_SIZE)
    for start_index in range(0, total_samples - SEGMENT_SIZE + 1, step_samples):
        end_index = start_index + SEGMENT_SIZE
        for ch in range(audio.shape[0]):
            segment = audio[ch, start_index:end_index]
            # Use the file's modification time to compute segment times.
            mod_time = os.path.getmtime(file_path)
            segment_start = datetime.datetime.utcfromtimestamp(
                mod_time
            ) + datetime.timedelta(seconds=start_index / SAMPLE_RATE)
            segment_end = datetime.datetime.utcfromtimestamp(
                mod_time
            ) + datetime.timedelta(seconds=end_index / SAMPLE_RATE)
            store_audio(segment, segment_start, segment_end, file_id, ch)


def process_directory():
    """
    Continuously scan the unprocessed directory for new sound files,
    record file-level metadata (using the timestamp from the filename if available),
    process each file into segments (using pre-extracted metadata),
    update its processing status, and move it to a processed directory while preserving its structure.
    """
    file_stable_threshold = 30
    processed_dir = "/mnt/audio_recordings/processed"
    unprocessed_dir = "/mnt/audio_recordings/unprocessed"
    logging.info(f"Scanning directory for sound files: {unprocessed_dir}")

    accepted_extensions = (".wav", ".mp3", ".flac", ".ogg")

    while True:
        for root, dirs, files in os.walk(unprocessed_dir):
            for file in files:
                if not file.lower().endswith(accepted_extensions):
                    continue
                file_path = os.path.join(root, file)

                # Check if this file has already been processed.
                cursor.execute(
                    "SELECT id FROM sensos.audio_files WHERE file_path = %s",
                    (file_path,),
                )
                if cursor.fetchone() is not None:
                    logging.warning(f"File already processed: {file_path}")
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
                    time.sleep(5)
                    continue

                # Extract recording timestamp from filename if possible.
                recording_timestamp = extract_timestamp_from_filename(file_path)
                if recording_timestamp is None:
                    recording_timestamp = mod_time

                # Read metadata only once here.
                try:
                    info = sf.info(file_path)
                except Exception as e:
                    logging.error(f"Error reading file info for {file_path}: {e}")
                    continue
                srate = info.samplerate
                channel_count = info.channels

                # Determine native audio format:
                # If the environment variable AUDIO_FORMAT_CODE is set, use it;
                # otherwise, derive from info.subtype.
                native_format = os.environ.get("AUDIO_FORMAT_CODE")
                if info.subtype in ["PCM_16", "PCM_S16LE", "PCM_S16BE"]:
                    native_format = "int16"
                elif info.subtype in ["PCM_24"]:
                    native_format = "int32"
                elif info.subtype in ["PCM_32", "S32_LE", "S32_BE"]:
                    native_format = "int32"
                elif info.subtype in ["FLOAT", "FLOAT32"]:
                    native_format = "float32"
                else:
                    logging.error(f"Unknown audio subtype: {info.subtype}")
                    raise ValueError(f"Unsupported audio byte layout: {info.subtype}")

                metadata = {"native_format": native_format, "samplerate": srate}

                # Record file-level metadata using the extracted timestamp.
                file_id = create_file_recording(
                    file_path,
                    srate,
                    native_format,
                    channel_count,
                    recording_timestamp,
                )
                logging.info(f"Created file recording record {file_id} for {file_path}")

                # Process the file into segments using the metadata.
                process_file(file_path, file_id, metadata)

                rel_path = os.path.relpath(file_path, unprocessed_dir)
                destination = os.path.join(processed_dir, rel_path)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                try:
                    shutil.move(file_path, destination)
                    logging.info(f"Moved {file_path} to {destination}")
                except Exception as e:
                    logging.error(f"Error moving {file_path} to {destination}: {e}")
                    continue

                # Update the audio_files table with the new file path.
                try:
                    cursor.execute(
                        "UPDATE sensos.audio_files SET file_path = %s WHERE id = %s",
                        (destination, file_id),
                    )
                    conn.commit()
                    logging.info(
                        f"Updated file recording record {file_id} with new path: {destination}"
                    )
                except Exception as e:
                    logging.error(
                        f"Error updating file recording record for {file_path}: {e}"
                    )
                    conn.rollback()

        time.sleep(5)


def cleanup():
    logging.info("Shutting down... Closing database connection.")
    cursor.close()
    conn.close()
    logging.info("Database connection closed. Exiting.")
    sys.exit(0)


if __name__ == "__main__":
    logging.info("Starting file scanning mode.")
    try:
        process_directory()
    except KeyboardInterrupt:
        cleanup()
