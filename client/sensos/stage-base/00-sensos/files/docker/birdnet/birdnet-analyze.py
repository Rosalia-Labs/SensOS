import os
import time
import json
import numpy as np
import psycopg
import tflite_runtime.interpreter as tflite
import librosa
import datetime
import logging
import sys

# Configure logging.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Database connection details (using a connection string to match the updated schema).
DB_PARAMS = (
    f"dbname={os.environ.get('POSTGRES_DB', 'postgres')} "
    f"user={os.environ.get('POSTGRES_USER', 'postgres')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'sensos')} "
    f"host={os.environ.get('DB_HOST', 'sensos-client-database')} "
    f"port={os.environ.get('DB_PORT', '5432')}"
)

# Load BirdNET TFLite model.
MODEL_PATH = "/app/model/V2.4/BirdNET_GLOBAL_6K_V2.4_Model_FP32.tflite"
interpreter = tflite.Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()

# Get input and output tensors.
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()

# BirdNET model expects 48 kHz, mono, float32.
SAMPLE_RATE = 48000
SEGMENT_DURATION = 3  # seconds
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION  # Number of samples per segment.

LABELS_PATH = "/app/model/V2.4/BirdNET_GLOBAL_6K_V2.4_Labels.txt"
with open(LABELS_PATH, "r") as f:
    LABELS = [line.strip() for line in f.readlines()]


def wait_for_schema():
    """Wait until the 'sensos' schema exists in the database before proceeding."""
    while True:
        try:
            with psycopg.connect(DB_PARAMS) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'sensos';"
                    )
                    if cur.fetchone():
                        print("Schema 'sensos' exists. Proceeding...")
                        return
                    else:
                        print("Waiting for schema 'sensos' to be created...")
        except psycopg.OperationalError as e:
            print(f"Database connection failed: {e}. Retrying in 5 seconds...")
        time.sleep(5)


def initialize_schema():
    """Ensure the required database schema and tables exist."""
    wait_for_schema()  # Ensure 'sensos' schema exists before proceeding.
    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            # Table for embeddings.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_embeddings (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.raw_audio(segment_id) ON DELETE CASCADE,
                    vector vector(1024) NOT NULL
                );
                """
            )
            # Table for species scores: Note "score" replaces "confidence".
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_scores (
                    segment_id INTEGER REFERENCES sensos.raw_audio(segment_id) ON DELETE CASCADE,
                    species TEXT NOT NULL,
                    score FLOAT NOT NULL,
                    PRIMARY KEY (segment_id, species)
                );
                """
            )
            conn.commit()
    print("Database schema verified.")


def get_unprocessed_audio():
    """
    Fetch unprocessed audio data from the database along with the native audio format.
    This query joins the raw_audio table with audio_segments and audio_files (which stores the native_format).
    """
    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ra.segment_id, ra.data, af.native_format
                FROM sensos.raw_audio ra
                JOIN sensos.audio_segments r ON ra.segment_id = r.id
                JOIN sensos.audio_files af ON r.file_id = af.id
                WHERE ra.segment_id NOT IN (SELECT segment_id FROM sensos.birdnet_embeddings);
                """
            )
            return cur.fetchall()


def process_audio(audio_bytes, audio_format):
    """
    Converts raw audio bytes into a float32 numpy array normalized to [-1, 1].

    The conversion is based on the supplied native audio format code, which may be one of:

      - FLOAT_LE / FLOAT_BE: Reads as np.float32.
      - FLOAT64_LE / FLOAT64_BE: Reads as np.float64 then cast to np.float32.
      - S8:  8-bit signed integer, scaled by 128.
      - U8:  8-bit unsigned integer, offset by 128 then scaled by 128.
      - S16_LE / S16_BE: 16-bit signed integer, scaled by 32768.
      - U16_LE / U16_BE: 16-bit unsigned integer, offset by 32768 then scaled by 32768.
      - S24_LE / S24_BE or S24_3LE / S24_3BE: Assumed stored in a 32-bit container, scaled by 8388608.
      - U24_LE / U24_BE or U24_3LE / U24_3BE: 24-bit unsigned, offset by 8388608 then scaled by 8388608.
      - S32_LE / S32_BE: 32-bit signed integer, scaled by 2147483648.
      - U32_LE / U32_BE: 32-bit unsigned, offset by 2147483648 then scaled by 2147483648.

    If the audio format is unsupported or the resulting sample count does not equal SEGMENT_SIZE,
    the function logs an error and exits the process.
    """
    if audio_format in ["FLOAT_LE", "FLOAT_BE"]:
        audio_np = np.frombuffer(audio_bytes, dtype=np.float32)
        if np.any(np.abs(audio_np) > 1):
            max_abs = np.max(np.abs(audio_np))
            if max_abs != 0:
                logging.info(
                    f"Normalizing {audio_format} audio data (max abs {max_abs} > 1)."
                )
                audio_np = audio_np / max_abs
    elif audio_format in ["FLOAT64_LE", "FLOAT64_BE"]:
        audio_np = np.frombuffer(audio_bytes, dtype=np.float64).astype(np.float32)
        if np.any(np.abs(audio_np) > 1):
            max_abs = np.max(np.abs(audio_np))
            if max_abs != 0:
                logging.info(
                    f"Normalizing {audio_format} audio data (max abs {max_abs} > 1) after casting."
                )
                audio_np = audio_np / max_abs
    elif audio_format == "S8":
        audio_np = np.frombuffer(audio_bytes, dtype=np.int8).astype(np.float32) / 128.0
    elif audio_format == "U8":
        audio_np = np.frombuffer(audio_bytes, dtype=np.uint8).astype(np.float32)
        audio_np = (audio_np - 128) / 128.0
    elif audio_format in ["S16_LE", "S16_BE"]:
        audio_np = (
            np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )
    elif audio_format in ["U16_LE", "U16_BE"]:
        audio_np = np.frombuffer(audio_bytes, dtype=np.uint16).astype(np.float32)
        audio_np = (audio_np - 32768) / 32768.0
    elif audio_format in ["S24_LE", "S24_BE", "S24_3LE", "S24_3BE"]:
        audio_np = (
            np.frombuffer(audio_bytes, dtype=np.int32).astype(np.float32) / 8388608.0
        )
    elif audio_format in ["U24_LE", "U24_BE", "U24_3LE", "U24_3BE"]:
        audio_np = np.frombuffer(audio_bytes, dtype=np.uint32).astype(np.float32)
        audio_np = (audio_np - 8388608) / 8388608.0
    elif audio_format in ["S32_LE", "S32_BE"]:
        audio_np = (
            np.frombuffer(audio_bytes, dtype=np.int32).astype(np.float32) / 2147483648.0
        )
    elif audio_format in ["U32_LE", "U32_BE"]:
        audio_np = np.frombuffer(audio_bytes, dtype=np.uint32).astype(np.float32)
        audio_np = (audio_np - 2147483648) / 2147483648.0
    else:
        logging.error(f"Unsupported audio format: {audio_format}. Exiting.")
        sys.exit(1)

    if len(audio_np) != SEGMENT_SIZE:
        logging.error(
            f"Segment length mismatch: expected {SEGMENT_SIZE}, got {len(audio_np)}. Exiting."
        )
        sys.exit(1)

    if audio_np.dtype != np.float32:
        logging.warning(f"Converting audio from {audio_np.dtype} to float32.")
    audio_np = audio_np.astype(np.float32)

    if len(audio_np) != SEGMENT_SIZE:
        logging.error(
            f"Segment length mismatch: expected {SEGMENT_SIZE}, got {len(audio_np)}. Exiting."
        )
        sys.exit(1)

    return audio_np


def flat_sigmoid(x, sensitivity=-1, bias=1.0):
    """
    Applies a flat sigmoid function to the input array with a bias shift.
    """
    transformed_bias = (bias - 1.0) * 10.0
    return 1 / (1.0 + np.exp(sensitivity * np.clip(x + transformed_bias, -20, 20)))


def invoke_interpreter(audio_segment):
    """
    Runs BirdNET model inference on a single 3-second segment and extracts embeddings and species scores.
    """
    input_data = np.expand_dims(audio_segment, axis=0).astype(np.float32)
    interpreter.set_tensor(input_details[0]["index"], input_data)

    # Debug
    print(
        f"Audio dtype: {audio_segment.dtype}, max={np.max(audio_segment):.3f}, min={np.min(audio_segment):.3f}"
    )
    print(f"Any NaNs? {np.isnan(audio_segment).any()}")
    print(f"Shape: {audio_segment.shape}, Expected: ({SEGMENT_SIZE},)")

    interpreter.invoke()
    score_output_index = output_details[0]["index"]
    embedding_output_index = score_output_index - 1
    scores = interpreter.get_tensor(score_output_index)
    embedding = interpreter.get_tensor(embedding_output_index)
    print(f"Species scores shape: {scores.shape}")  # Expected: (1, 6522)
    print(f"Embedding shape: {embedding.shape}")  # Expected: (1, 1024)
    embedding_flat = embedding.flatten()
    scores_flat = flat_sigmoid(scores.flatten())
    species_scores = {LABELS[i]: scores_flat[i] for i in range(len(scores_flat))}
    return embedding_flat, species_scores


def store_results(segment_id, embeddings, scores, top_n=5):
    """Stores embeddings and top-N species scores in the database."""
    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            if embeddings is not None:
                cur.execute(
                    """
                    INSERT INTO sensos.birdnet_embeddings (segment_id, vector)
                    VALUES (%s, %s);
                    """,
                    (segment_id, embeddings.tolist()),
                )
            top_species = sorted(scores.items(), key=lambda x: x[1], reverse=True)[
                :top_n
            ]
            for species, score in top_species:
                cur.execute(
                    """
                    INSERT INTO sensos.birdnet_scores (segment_id, species, score)
                    VALUES (%s, %s, %s);
                    """,
                    (segment_id, species, score),
                )
            conn.commit()


def main():
    initialize_schema()  # Ensure database structure exists at startup.
    while True:
        print("Checking for new audio segments...")
        unprocessed_audio = get_unprocessed_audio()
        if not unprocessed_audio:
            print("No new audio. Sleeping...")
            time.sleep(5)
            continue

        for segment_id, audio_bytes, native_format in unprocessed_audio:
            print(
                f"Processing segment {segment_id} (native format: {native_format})..."
            )
            # If process_audio fails to produce valid data, it will exit.
            audio_np = process_audio(audio_bytes, native_format)
            embeddings, scores = invoke_interpreter(audio_np)
            store_results(segment_id, embeddings, scores)
            print(f"Stored embeddings for segment {segment_id}")

        print("Processing complete. Sleeping...")
        time.sleep(5)


if __name__ == "__main__":
    main()
