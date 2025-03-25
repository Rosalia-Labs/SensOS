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


def rescale(data, subtype: str) -> np.ndarray:
    """
    Given a NumPy array of raw audio data and the soundfile subtype string,
    apply an affine transformation (subtract an offset and divide by a scale)
    to convert the raw data to float32 values in the range [-1, 1].

    The mapping (offset, scale) is defined for each subtype:
      - For signed types (e.g., "PCM_16"), no offset is applied.
      - For unsigned types (e.g., "PCM_U8"), an offset is subtracted before scaling.
      - For floating point types ("FLOAT", "DOUBLE"), no scaling is applied.

    Returns:
      A NumPy array of type float32 with values normalized to [-1, 1].
    """
    subtype_up = subtype.upper()
    mapping = {
        "PCM_S8": (0, 128),
        "PCM_16": (0, 32768),
        "PCM_24": (0, 8388608),
        "PCM_32": (0, 2147483648),
        "PCM_U8": (128, 128),
        "FLOAT": (0, 1.0),
        "DOUBLE": (0, 1.0),
        "ULAW": (0, 128),
        "ALAW": (0, 128),
        "IMA_ADPCM": (0, 32768),
        "MS_ADPCM": (0, 32768),
        "GSM610": (0, 128),
        "VOX_ADPCM": (0, 128),
        "NMS_ADPCM_16": (0, 32768),
        "NMS_ADPCM_24": (0, 8388608),
        "NMS_ADPCM_32": (0, 2147483648),
        "G721_32": (0, 2147483648),
        "G723_24": (0, 8388608),
        "G723_40": (0, 2147483648),
        "DWVW_12": (0, 2048),
        "DWVW_16": (0, 32768),
        "DWVW_24": (0, 8388608),
        "DWVW_N": (0, 32768),
        "DPCM_8": (0, 128),
        "DPCM_16": (0, 32768),
        "VORBIS": (0, 1.0),
        "OPUS": (0, 1.0),
        "ALAC_16": (0, 32768),
        "ALAC_20": (0, 524288),
        "ALAC_24": (0, 8388608),
        "ALAC_32": (0, 2147483648),
        "MPEG_LAYER_I": (0, 2147483648),
        "MPEG_LAYER_II": (0, 2147483648),
        "MPEG_LAYER_III": (0, 2147483648),
    }
    offset, scale = mapping.get(subtype_up, (0, 1.0))
    return (data.astype(np.float32) - offset) / scale


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
    logger.info("Database schema verified.")


def get_unprocessed_audio():
    """
    Query the database to retrieve unprocessed audio segments.
    Each segment object includes:
      - segment_id
      - data (raw audio bytes)
      - subtype (e.g. "PCM_16", "PCM_24", "FLOAT", etc.)
      - storage_type (a string representing the NumPy type used, e.g. "int16", "int32", or "float32")
    """
    if MOCK_DATA:
        logger.info("MOCK_DATA enabled: generating 3 fake segments.")
        return [
            {
                "segment_id": i,
                "data": None,
                "subtype": "PCM_16",
                "storage_type": "int16",
            }
            for i in range(1, 4)
        ]

    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ra.segment_id, ra.data, af.subtype, af.storage_type
                FROM sensos.raw_audio ra
                JOIN sensos.audio_segments r ON ra.segment_id = r.id
                JOIN sensos.audio_files af ON r.file_id = af.id
                WHERE ra.segment_id NOT IN (SELECT segment_id FROM sensos.birdnet_embeddings);
                """
            )
            rows = cur.fetchall()

    segments = []
    for row in rows:
        segment = {
            "segment_id": row[0],
            "data": row[1],
            "subtype": row[2],
            "storage_type": row[3],
        }
        segments.append(segment)
    return segments


def format_audio_data(audio_bytes, storage_type: str, subtype: str):
    """
    Process an audio segment based on the given storage_type and subtype.

    - Converts the raw bytes to a NumPy array using the dtype specified by storage_type.
    - Uses rescale(data, subtype) to perform an affine transformation to normalize the data
      to the [-1, 1] range.

    Returns:
      A NumPy array of type float32 of length SEGMENT_SIZE with values scaled to [-1, 1].
    """
    if MOCK_DATA:
        return np.random.uniform(-1, 1, SEGMENT_SIZE).astype(np.float32)

    logger.info(
        f"Processing segment with storage type {storage_type} and subtype {subtype}"
    )
    try:
        audio_np = np.frombuffer(audio_bytes, dtype=np.dtype(storage_type))
        audio_np = rescale(audio_np, subtype)

        if len(audio_np) != SEGMENT_SIZE:
            logger.error(
                f"Segment length mismatch: expected {SEGMENT_SIZE}, got {len(audio_np)}"
            )
            sys.exit(1)

        return audio_np
    except Exception as e:
        logger.error(f"Audio processing error: {e}")
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
    logger.info(f"Stored embeddings and scores for segment {segment_id}.")


def main():
    initialize_schema()
    while True:
        logger.info("Checking for new audio segments...")
        segments = get_unprocessed_audio()
        if not segments:
            logger.info("No new segments found. Sleeping...")
            time.sleep(5)
            continue

        for segment in segments:
            segment_id = segment["segment_id"]
            audio_bytes = segment["data"]
            subtype = segment["subtype"]
            storage_type = segment["storage_type"]
            logger.info(
                f"Processing segment {segment_id} (storage type: {storage_type}, subtype: {subtype})"
            )
            audio_np = format_audio_data(audio_bytes, storage_type, subtype)
            embeddings, scores = invoke_interpreter(audio_np)
            store_results(segment_id, embeddings, scores)

        logger.info("Processing complete. Sleeping...")
        time.sleep(5)


if __name__ == "__main__":
    main()
