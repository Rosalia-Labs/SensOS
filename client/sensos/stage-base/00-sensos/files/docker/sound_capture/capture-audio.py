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
DB_PARAMS = (
    f"dbname={os.environ['POSTGRES_DB']} "
    f"user={os.environ['POSTGRES_USER']} "
    f"password={os.environ['POSTGRES_PASSWORD']} "
    f"host={os.environ['DB_HOST']} "
    f"port={os.environ['DB_PORT']}"
)

# Audio settings.
SAMPLE_RATE = 48000  # expected sample rate
SEGMENT_DURATION = 3  # seconds per segment
STEP_SIZE = 1  # seconds; smaller value creates overlapping segments
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION
STEP_SIZE_FRAMES = SAMPLE_RATE * STEP_SIZE

# Lookup table for storage types.
dtype_lookup = {
    "PCM_S8": np.int16,  # 8-bit integer
    "PCM_16": np.int16,  # 16-bit integer
    "PCM_24": np.int32,  # 24-bit integer (>16 bits)
    "PCM_32": np.int32,  # 32-bit integer (>16 bits)
    "PCM_U8": np.int16,  # 8-bit unsigned integer
    "FLOAT": np.float32,  # floating point
    "DOUBLE": np.float32,  # floating point (convert 64-bit to float32)
    "ULAW": np.int16,  # typically 8-bit encoded
    "ALAW": np.int16,  # typically 8-bit encoded
    "IMA_ADPCM": np.int16,  # assume 16-bit or lower after decoding
    "MS_ADPCM": np.int16,  # assume 16-bit or lower after decoding
    "GSM610": np.int16,  # usually 8-bit resolution
    "VOX_ADPCM": np.int16,
    "NMS_ADPCM_16": np.int16,  # 16-bit
    "NMS_ADPCM_24": np.int32,  # >16-bit
    "NMS_ADPCM_32": np.int32,  # >16-bit
    "G721_32": np.int32,  # 32-bit integer
    "G723_24": np.int32,  # 24-bit integer (>16 bits)
    "G723_40": np.int32,  # 40-bit integer, best match with np.int32
    "DWVW_12": np.int16,  # 12-bit, <=16
    "DWVW_16": np.int16,  # 16-bit
    "DWVW_24": np.int32,  # 24-bit
    "DWVW_N": np.int16,  # assume <=16 bits
    "DPCM_8": np.int16,  # 8-bit
    "DPCM_16": np.int16,  # 16-bit
    "VORBIS": np.float32,  # decoded as float32
    "OPUS": np.float32,  # decoded as float32
    "ALAC_16": np.int16,  # 16-bit
    "ALAC_20": np.int32,  # >16-bit
    "ALAC_24": np.int32,  # >16-bit
    "ALAC_32": np.int32,  # >16-bit
    "MPEG_LAYER_I": np.int32,  # assume integer, >16 bits
    "MPEG_LAYER_II": np.int32,
    "MPEG_LAYER_III": np.int32,
}


def get_storage_type(subtype: str):
    """
    Given a soundfile subtype string, returns the numpy dtype to use:
      - np.float32 for floating point subtypes ("FLOAT", "DOUBLE").
      - np.int32 for integer subtypes with bit depth > 16.
      - np.int16 for integer subtypes with bit depth <= 16.
    If the subtype is not found in the lookup, defaults to np.float32.
    """
    return dtype_lookup.get(subtype.upper(), np.float32)


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

    # Create tables
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.audio_files (
            id SERIAL PRIMARY KEY,
            file_path TEXT NOT NULL,
            file_mod_time TIMESTAMPTZ,
            sample_rate INT NOT NULL,
            channel_count INT NOT NULL,
            frames BIGINT,
            duration DOUBLE PRECISION,
            file_format TEXT,         
            subtype TEXT,
            endian TEXT,
            format_info TEXT,
            subtype_info TEXT,
            storage_type TEXT,
            sections INT,
            extra_info TEXT,
            processed_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
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

    # Recommended indexes (auto-named)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS audio_files_file_path_index ON sensos.audio_files(file_path);"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS audio_segments_file_id_index ON sensos.audio_segments(file_id);"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS audio_segments_t_begin_index ON sensos.audio_segments(t_begin);"
    )

    conn.commit()
    logging.info("Database schema initialized.")


initialize_schema()


def record_file_info(info, recording_timestamp):
    """
    Inserts a record into the sensos.audio_files table using metadata from
    the provided info object (returned by sf.info()) and the given recording_timestamp.
    Also stores the output of get_storage_type.
    Returns the new file record's id.
    """
    full_path = os.path.abspath(info.name)
    # Get the storage type as a string (e.g., "int16", "int32", or "float32").
    storage_type = get_storage_type(info.subtype).__name__

    cursor.execute(
        """
        INSERT INTO sensos.audio_files 
            (file_path, file_mod_time, sample_rate, channel_count,
             frames, duration, file_format, subtype, endian, format_info, subtype_info, storage_type, sections, extra_info)
        VALUES (%s, to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (
            full_path,
            recording_timestamp,
            info.samplerate,
            info.channels,
            info.frames,
            info.duration,
            info.format,
            info.subtype,
            info.endian,
            info.format_info,
            info.subtype_info,
            storage_type,
            info.sections,
            str(info.extra_info),
        ),
    )
    file_id = cursor.fetchone()[0]
    conn.commit()
    logging.info(f"Created file recording record {file_id} for {full_path}")
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


def process_file(info, file_id):
    """
    Load an audio file and split it into overlapping segments using the preallocated buffer.
    Computes segment start and end times based on the file's modification time.
    """
    file_path = os.path.abspath(info.name)
    logging.info(f"Processing file: {file_path}")

    mod_time = os.path.getmtime(file_path)
    blksz = SEGMENT_SIZE
    ovlp = SEGMENT_SIZE - STEP_SIZE_FRAMES
    read_dtype = get_storage_type(info.subtype)

    buf = np.empty((SEGMENT_SIZE, info.channels), dtype=read_dtype)

    with sf.SoundFile(file_path) as f:
        block_index = 0
        for block in f.blocks(
            frames=blksz, overlap=ovlp, dtype=read_dtype, fill_value=0, out=buf
        ):
            start_frame = block_index * STEP_SIZE_FRAMES
            end_frame = start_frame + SEGMENT_SIZE

            start_time = datetime.datetime.utcfromtimestamp(
                mod_time
            ) + datetime.timedelta(seconds=start_frame / info.samplerate)
            end_time = datetime.datetime.utcfromtimestamp(
                mod_time
            ) + datetime.timedelta(seconds=end_frame / info.samplerate)

            for ch in range(info.channels):
                segment = block[:, ch]
                store_audio(segment, start_time, end_time, file_id, ch)

            block_index += 1


def process_directory():
    """
    Continuously scan the unprocessed directory for new sound files,
    record file-level metadata, process each file into segments,
    update its processing status, and move it to a processed directory.
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
                rel_path = os.path.relpath(file_path, unprocessed_dir)

                cursor.execute(
                    "SELECT id FROM sensos.audio_files WHERE file_path = %s",
                    (rel_path,),
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

                recording_timestamp = extract_timestamp_from_filename(file_path)
                if recording_timestamp is None:
                    recording_timestamp = mod_time

                try:
                    info = sf.info(file_path)
                except Exception as e:
                    logging.error(f"Error reading file info for {file_path}: {e}")
                    continue

                file_id = record_file_info(info, recording_timestamp)
                logging.info(f"Created file recording record {file_id} for {file_path}")

                process_file(info, file_id)

                destination = os.path.join(processed_dir, rel_path)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                try:
                    shutil.move(file_path, destination)
                    logging.info(f"Moved {file_path} to {destination}")
                except Exception as e:
                    logging.error(f"Error moving {file_path} to {destination}: {e}")
                    continue

                try:
                    cursor.execute(
                        "UPDATE sensos.audio_files SET file_path = %s WHERE id = %s",
                        (rel_path, file_id),
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


def restore_untracked_processed_files():
    """
    Scans the processed directory and moves any files that are not in the database
    back into the unprocessed directory for reprocessing.
    """
    processed_dir = "/mnt/audio_recordings/processed"
    unprocessed_dir = "/mnt/audio_recordings/unprocessed"
    accepted_extensions = (".wav", ".mp3", ".flac", ".ogg")

    logging.info("Scanning processed directory for untracked files...")

    for root, dirs, files in os.walk(processed_dir):
        for file in files:
            if not file.lower().endswith(accepted_extensions):
                continue

            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, processed_dir)
            dest_path = os.path.join(unprocessed_dir, rel_path)

            cursor.execute(
                "SELECT id FROM sensos.audio_files WHERE file_path = %s",
                (rel_path,),
            )

            if cursor.fetchone() is None:
                logging.warning(f"Restoring untracked file: {file_path}")
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                try:
                    shutil.move(file_path, dest_path)
                    logging.info(f"Moved {file_path} back to {dest_path}")
                except Exception as e:
                    logging.error(f"Failed to move {file_path} to {dest_path}: {e}")


def convert_absolute_paths_to_relative():
    """
    Scans the sensos.audio_files table and converts absolute file paths under
    /mnt/audio_recordings/processed to relative paths.
    """
    base_dir = os.path.abspath("/mnt/audio_recordings/processed")

    cursor.execute("SELECT id, file_path FROM sensos.audio_files;")
    updates = []
    for row in cursor.fetchall():
        file_id, file_path = row
        if file_path.startswith(base_dir + os.sep):
            rel_path = os.path.relpath(file_path, base_dir)
            updates.append((rel_path, file_id))

    for rel_path, file_id in updates:
        cursor.execute(
            "UPDATE sensos.audio_files SET file_path = %s WHERE id = %s;",
            (rel_path, file_id),
        )

    if updates:
        conn.commit()
        logging.info(f"Converted {len(updates)} file paths to relative.")
    else:
        logging.info("No file paths required conversion.")


def cleanup():
    logging.info("Shutting down... Closing database connection.")
    cursor.close()
    conn.close()
    logging.info("Database connection closed. Exiting.")
    sys.exit(0)


if __name__ == "__main__":
    logging.info("Starting file scanning mode.")
    try:
        convert_absolute_paths_to_relative()
        restore_untracked_processed_files()
        process_directory()
    except KeyboardInterrupt:
        cleanup()
