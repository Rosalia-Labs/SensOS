import os
import time
import json
import numpy as np
import psycopg
import tflite_runtime.interpreter as tflite
import librosa
import logging
import sys
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("birdnet-inference")

# Database connection details
DB_PARAMS = (
    f"dbname={os.environ.get('POSTGRES_DB', 'postgres')} "
    f"user={os.environ.get('POSTGRES_USER', 'postgres')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'sensos')} "
    f"host={os.environ.get('DB_HOST', 'sensos-client-database')} "
    f"port={os.environ.get('DB_PORT', '5432')}"
)

# BirdNET model
MODEL_PATH = "/model/V2.4/BirdNET_GLOBAL_6K_V2.4_Model_FP32.tflite"
interpreter = tflite.Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()

input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()

SAMPLE_RATE = 48000
SEGMENT_DURATION = 3
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION

LABELS_PATH = "/model/V2.4/BirdNET_GLOBAL_6K_V2.4_Labels.txt"
with open(LABELS_PATH, "r") as f:
    LABELS = [
        f"{common} ({sci})" if "_" in line else line.strip()
        for line in f.readlines()
        for sci, common in [line.strip().split("_", 1)]
    ]

ROOT = Path("/mnt/audio_recordings")
CATALOGED = ROOT / "cataloged"


def wait_for_schema():
    while True:
        try:
            with psycopg.connect(DB_PARAMS) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'sensos';"
                    )
                    if cur.fetchone():
                        logger.info("Schema 'sensos' exists. Proceeding...")
                        return
                    else:
                        logger.info("Waiting for schema 'sensos' to be created...")
        except psycopg.OperationalError as e:
            logger.warning(f"Database connection failed: {e}. Retrying...")
        time.sleep(5)


def initialize_schema():
    wait_for_schema()
    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_embeddings (
                    file_path TEXT NOT NULL REFERENCES sensos.audio_files(file_path) ON DELETE CASCADE,
                    channel INT NOT NULL,
                    start_frame BIGINT NOT NULL,
                    vector vector(1024) NOT NULL,
                    PRIMARY KEY (file_path, channel, start_frame)
                );
            """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_scores (
                    file_path TEXT NOT NULL REFERENCES sensos.audio_files(file_path) ON DELETE CASCADE,
                    channel INT NOT NULL,
                    start_frame BIGINT NOT NULL,
                    label TEXT NOT NULL,
                    score FLOAT NOT NULL,
                    PRIMARY KEY (file_path, channel, start_frame, label)
                );
            """
            )
            conn.commit()
    logger.info("Database schema verified.")


def get_cataloged_audio():
    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT file_path, channel_count, duration FROM sensos.audio_files
                WHERE file_path NOT IN (
                    SELECT DISTINCT file_path FROM sensos.birdnet_embeddings
                );
            """
            )
            rows = cur.fetchall()

    segments = []
    for file_path, channels, duration in rows:
        abs_path = CATALOGED / Path(file_path).relative_to("cataloged")
        total_frames = int(duration * SAMPLE_RATE)
        step = SAMPLE_RATE  # 1s step
        seg_size = SEGMENT_SIZE  # 3s window
        for start in range(0, total_frames - seg_size + 1, step):
            for ch in range(channels):
                segments.append(
                    {
                        "file_path": file_path,
                        "abs_path": abs_path,
                        "channel": ch,
                        "start_frame": start,
                    }
                )
    return segments


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

    # Normalize scores to probabilities
    total = np.sum(scores_flat)
    if total > 0:
        probs = scores_flat / total
    else:
        probs = np.zeros_like(scores_flat)

    # Shannon entropy (base 2)
    nonzero_probs = probs[probs > 0]
    entropy = -np.sum(nonzero_probs * np.log2(nonzero_probs))

    # Hill number order 1 = 2^entropy
    hill_number = float(2**entropy)

    return embedding_flat, species_scores, entropy, hill_number


def store_results(file_path, channel, start_frame, embeddings, scores, top_n=5):
    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            if embeddings is not None:
                cur.execute(
                    """
                    INSERT INTO sensos.birdnet_embeddings (file_path, channel, start_frame, vector)
                    VALUES (%s, %s, %s, %s);
                """,
                    (file_path, channel, start_frame, embeddings.tolist()),
                )
            for label, score in sorted(
                scores.items(), key=lambda x: x[1], reverse=True
            )[:top_n]:
                cur.execute(
                    """
                    INSERT INTO sensos.birdnet_scores (file_path, channel, start_frame, label, score)
                    VALUES (%s, %s, %s, %s, %s);
                """,
                    (file_path, channel, start_frame, label, score),
                )
            conn.commit()
    logger.info(
        f"Stored embeddings and scores for {file_path}, ch {channel}, frame {start_frame}."
    )


def get_next_unprocessed_file():
    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT af.file_path
                FROM sensos.audio_files af
                LEFT JOIN sensos.birdnet_embeddings be
                ON af.file_path = be.file_path
                WHERE be.file_path IS NULL
                ORDER BY af.processed_time
                LIMIT 1;
                """
            )
            row = cur.fetchone()
            return row[0] if row else None


import soundfile as sf


def main():
    initialize_schema()
    while True:
        logger.info("Checking for next unprocessed file...")
        file_path = get_next_unprocessed_file()
        if not file_path:
            logger.info("No unprocessed files found. Sleeping...")
            time.sleep(60)
            continue

        abs_path = CATALOGED / Path(file_path).relative_to("cataloged")

        try:
            info = sf.info(abs_path.as_posix())
            channels = info.channels
            duration = info.frames / info.samplerate
        except Exception as e:
            logger.error(f"Failed to read audio info from {file_path}: {e}")
            continue

        total_frames = int(duration * SAMPLE_RATE)
        step = SAMPLE_RATE  # 1s
        seg_size = SEGMENT_SIZE  # 3s

        logger.info(f"Processing file: {file_path} ({channels} ch, {duration:.2f} s)")

        for start in range(0, total_frames - seg_size + 1, step):
            for ch in range(channels):
                logger.info(f"Processing segment: ch {ch}, frame {start}")
                try:
                    y, sr = librosa.load(
                        abs_path.as_posix(),
                        sr=SAMPLE_RATE,
                        mono=False,
                        offset=start / SAMPLE_RATE,
                        duration=SEGMENT_DURATION,
                    )
                    if y.ndim == 1:
                        if ch != 0:
                            continue
                        audio_segment = y
                    else:
                        if ch >= y.shape[0]:
                            continue
                        audio_segment = y[ch]

                    if len(audio_segment) != SEGMENT_SIZE:
                        continue

                    embeddings, scores = invoke_interpreter(audio_segment)
                    store_results(file_path, ch, start, embeddings, scores)

                except Exception as e:
                    logger.error(
                        f"Failed segment {file_path}, ch {ch}, frame {start}: {e}"
                    )

        logger.info(f"Completed processing file: {file_path}")


if __name__ == "__main__":
    main()
