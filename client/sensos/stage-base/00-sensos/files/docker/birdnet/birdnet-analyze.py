import os
import time
import json
import numpy as np
import psycopg
import tflite_runtime.interpreter as tflite
import librosa
import logging
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Database connection details
DB_PARAMS = (
    f"dbname={os.environ.get('POSTGRES_DB', 'postgres')} "
    f"user={os.environ.get('POSTGRES_USER', 'postgres')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'sensos')} "
    f"host={os.environ.get('DB_HOST', 'sensos-client-database')} "
    f"port={os.environ.get('DB_PORT', '5432')}"
)

# BirdNET model
MODEL_PATH = "/app/model/V2.4/BirdNET_GLOBAL_6K_V2.4_Model_FP32.tflite"
interpreter = tflite.Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()

input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()

SAMPLE_RATE = 48000
SEGMENT_DURATION = 3
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION

LABELS_PATH = "/app/model/V2.4/BirdNET_GLOBAL_6K_V2.4_Labels.txt"
with open(LABELS_PATH, "r") as f:
    LABELS = [line.strip() for line in f.readlines()]

MOCK_DATA = os.getenv("MOCK_DATA", "").lower() in ("1", "true", "yes")


def wait_for_schema():
    while True:
        try:
            with psycopg.connect(DB_PARAMS) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'sensos';"
                    )
                    if cur.fetchone():
                        logging.info("Schema 'sensos' exists. Proceeding...")
                        return
                    else:
                        logging.info("Waiting for schema 'sensos' to be created...")
        except psycopg.OperationalError as e:
            logging.warning(f"Database connection failed: {e}. Retrying...")
        time.sleep(5)


def initialize_schema():
    wait_for_schema()
    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_embeddings (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.raw_audio(segment_id) ON DELETE CASCADE,
                    vector vector(1024) NOT NULL
                );
                """
            )
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
    logging.info("Database schema verified.")


def get_unprocessed_audio():
    if MOCK_DATA:
        logging.info("MOCK_DATA enabled: generating 3 fake segments.")
        return [(i, None, "MOCK") for i in range(1, 4)]

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
    if MOCK_DATA:
        return np.random.uniform(-1, 1, SEGMENT_SIZE).astype(np.float32)

    logging.info(f"Processing segment with format {audio_format}")
    try:
        if audio_format in ["FLOAT_LE", "FLOAT_BE"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.float32)
        elif audio_format in ["FLOAT64_LE", "FLOAT64_BE"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.float64).astype(np.float32)
        elif audio_format == "S8":
            audio_np = (
                np.frombuffer(audio_bytes, dtype=np.int8).astype(np.float32) / 128
            )
        elif audio_format == "U8":
            audio_np = (
                np.frombuffer(audio_bytes, dtype=np.uint8).astype(np.float32) - 128
            ) / 128
        elif audio_format in ["S16_LE", "S16_BE"]:
            audio_np = (
                np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768
            )
        elif audio_format in ["U16_LE", "U16_BE"]:
            audio_np = (
                np.frombuffer(audio_bytes, dtype=np.uint16).astype(np.float32) - 32768
            ) / 32768
        elif audio_format in ["S24_LE", "S24_BE", "S24_3LE", "S24_3BE"]:
            audio_np = (
                np.frombuffer(audio_bytes, dtype=np.int32).astype(np.float32) / 8388608
            )
        elif audio_format in ["U24_LE", "U24_BE", "U24_3LE", "U24_3BE"]:
            audio_np = (
                np.frombuffer(audio_bytes, dtype=np.uint32).astype(np.float32) - 8388608
            ) / 8388608
        elif audio_format in ["S32_LE", "S32_BE"]:
            audio_np = (
                np.frombuffer(audio_bytes, dtype=np.int32).astype(np.float32)
                / 2147483648
            )
        elif audio_format in ["U32_LE", "U32_BE"]:
            audio_np = (
                np.frombuffer(audio_bytes, dtype=np.uint32).astype(np.float32)
                - 2147483648
            ) / 2147483648
        else:
            logging.error(f"Unsupported audio format: {audio_format}")
            sys.exit(1)

        if len(audio_np) != SEGMENT_SIZE:
            logging.error(
                f"Segment length mismatch: expected {SEGMENT_SIZE}, got {len(audio_np)}"
            )
            sys.exit(1)

        return audio_np.astype(np.float32)

    except Exception as e:
        logging.error(f"Audio processing error: {e}")
        sys.exit(1)


def flat_sigmoid(x, sensitivity=-1, bias=1.0):
    transformed_bias = (bias - 1.0) * 10.0
    return 1 / (1.0 + np.exp(sensitivity * np.clip(x + transformed_bias, -20, 20)))


def invoke_interpreter(audio_segment):
    input_data = np.expand_dims(audio_segment, axis=0).astype(np.float32)
    interpreter.set_tensor(input_details[0]["index"], input_data)

    interpreter.invoke()
    scores = interpreter.get_tensor(output_details[0]["index"])
    embedding = interpreter.get_tensor(output_details[0]["index"] - 1)

    scores_flat = flat_sigmoid(scores.flatten())
    embedding_flat = embedding.flatten()

    species_scores = {LABELS[i]: scores_flat[i] for i in range(len(scores_flat))}
    return embedding_flat, species_scores


def store_results(segment_id, embeddings, scores, top_n=5):
    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            if embeddings is not None:
                cur.execute(
                    "INSERT INTO sensos.birdnet_embeddings (segment_id, vector) VALUES (%s, %s);",
                    (segment_id, embeddings.tolist()),
                )
            for species, score in sorted(
                scores.items(), key=lambda x: x[1], reverse=True
            )[:top_n]:
                cur.execute(
                    "INSERT INTO sensos.birdnet_scores (segment_id, species, score) VALUES (%s, %s, %s);",
                    (segment_id, species, score),
                )
            conn.commit()
    logging.info(f"Stored embeddings and scores for segment {segment_id}.")


def main():
    initialize_schema()
    while True:
        logging.info("Checking for new audio segments...")
        segments = get_unprocessed_audio()
        if not segments:
            logging.info("No new segments found. Sleeping...")
            time.sleep(5)
            continue

        for segment_id, audio_bytes, native_format in segments:
            logging.info(f"Processing segment {segment_id} (format: {native_format})")
            audio_np = process_audio(audio_bytes, native_format)
            embeddings, scores = invoke_interpreter(audio_np)
            store_results(segment_id, embeddings, scores)

        logging.info("Processing complete. Sleeping...")
        time.sleep(5)


if __name__ == "__main__":
    main()
