import os
import time
import numpy as np
import psycopg
import tflite_runtime.interpreter as tflite
import librosa

# Database connection details
DB_PARAMS = {
    "dbname": os.getenv("POSTGRES_DB", "postgres"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "sensos"),
    "host": os.getenv("DB_HOST", "sensos-client-database"),
    "port": os.getenv("DB_PORT", "5432"),
}

# Load BirdNET TFLite model
MODEL_PATH = "/app/model/V2.4/BirdNET_GLOBAL_6K_V2.4_Model_FP32.tflite"
interpreter = tflite.Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()

# Get input and output tensors
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()

# BirdNET model expects 48 kHz, mono, float32
SAMPLE_RATE = 48000
SEGMENT_DURATION = 3  # seconds
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION  # Number of samples per segment

LABELS_PATH = "/app/model/V2.4/BirdNET_GLOBAL_6K_V2.4_Labels.txt"
with open(LABELS_PATH, "r") as f:
    LABELS = [line.strip() for line in f.readlines()]


def wait_for_schema():
    """Waits until the 'sensos' schema exists in the database before proceeding."""
    while True:
        try:
            with psycopg.connect(**DB_PARAMS) as conn:
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
    """Ensures the required database schema and tables exist."""
    wait_for_schema()  # Ensure 'sensos' schema exists before proceeding
    with psycopg.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            # Table for embeddings
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_embeddings (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.raw_audio(segment_id) ON DELETE CASCADE,
                    vector vector(1024) NOT NULL
                );
                """
            )
            # Table for species scores: Note "score" replaces "confidence"
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
    Fetches unprocessed audio data from the database along with the stored raw audio data type.
    The query joins the raw_audio table with recording_sessions to retrieve the 'raw_audio_dtype'
    field.
    """
    with psycopg.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ra.segment_id, ra.data, rs.raw_audio_dtype
                FROM sensos.raw_audio ra
                JOIN sensos.audio_segments r ON ra.segment_id = r.id
                JOIN sensos.recording_sessions rs ON r.session_id = rs.id
                WHERE ra.segment_id NOT IN (SELECT segment_id FROM sensos.birdnet_embeddings);
                """
            )
            return cur.fetchall()


def process_audio(audio_bytes, stored_dtype):
    """
    Converts raw audio bytes into a float32 numpy array normalized to [-1, 1].
    If the stored data type is 'int16', it converts and normalizes appropriately.
    If it's 'float32', it decodes directly.
    Returns None if the segment length is incorrect.
    """
    if stored_dtype == "float32":
        audio_np = np.frombuffer(audio_bytes, dtype=np.float32)
    elif stored_dtype == "int16":
        audio_np = (
            np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )
    else:
        print(f"Unsupported stored data type: {stored_dtype}. Skipping segment.")
        return None

    if len(audio_np) != SEGMENT_SIZE:
        print(
            f"Segment length mismatch: expected {SEGMENT_SIZE}, got {len(audio_np)}. Skipping."
        )
        return None

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
    """Stores embeddings and top-N species scores in the database, using the column 'score'."""
    with psycopg.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            # Store embeddings if available
            if embeddings is not None:
                cur.execute(
                    """
                    INSERT INTO sensos.birdnet_embeddings (segment_id, vector)
                    VALUES (%s, %s);
                    """,
                    (segment_id, embeddings.tolist()),
                )
            # Store top-N species scores, using "score" instead of "confidence"
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
    initialize_schema()  # Ensure database structure exists at startup
    while True:
        print("Checking for new audio segments...")
        unprocessed_audio = get_unprocessed_audio()
        if not unprocessed_audio:
            print("No new audio. Sleeping...")
            time.sleep(5)
            continue

        for segment_id, audio_bytes, stored_dtype in unprocessed_audio:
            print(f"Processing segment {segment_id} (stored type: {stored_dtype})...")
            audio_np = process_audio(audio_bytes, stored_dtype)
            if audio_np is None:
                continue

            embeddings, scores = invoke_interpreter(audio_np)
            store_results(segment_id, embeddings, scores)
            print(f"Stored embeddings for segment {segment_id}")

        print("Processing complete. Sleeping...")
        time.sleep(5)


if __name__ == "__main__":
    main()
