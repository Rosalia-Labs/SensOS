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
    "host": os.getenv("DB_HOST", "sensos-client-test-database"),
    "port": os.getenv("DB_PORT", "5432"),
}

# Load BirdNET TFLite model
MODEL_PATH = "/app/model/BirdNET_GLOBAL_6K_V2.4_Model_FP32.tflite"
interpreter = tflite.Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()

# Get input and output tensors
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()

# BirdNET model expects 48 kHz, mono, float32
SAMPLE_RATE = 48000
SEGMENT_DURATION = 3  # seconds
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION  # Number of samples per segment


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

        time.sleep(5)  # Wait before checking again


def initialize_schema():
    """Ensures the required database schema and tables exist."""
    wait_for_schema()  # Ensure 'sensos' schema exists before proceeding

    with psycopg.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.predictions (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.raw_audio(segment_id) ON DELETE CASCADE,
                    scores vector(1024) NOT NULL
                );
            """
            )
            conn.commit()
    print("Database schema verified.")


def get_unprocessed_audio():
    """Fetches unprocessed audio data from the database."""
    with psycopg.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT segment_id, data FROM sensos.raw_audio
                WHERE segment_id NOT IN (SELECT segment_id FROM sensos.predictions);
            """
            )
            return cur.fetchall()


def process_audio(audio_bytes):
    """Decodes BYTEA audio, resamples if needed, and normalizes to float32."""
    # Convert byte data to numpy array
    audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)

    # Normalize audio to [-1, 1]
    audio_np /= 32768.0

    # Resample to 48 kHz if needed
    audio_np = librosa.resample(audio_np, orig_sr=SAMPLE_RATE, target_sr=SAMPLE_RATE)

    return audio_np


def predict_species(audio_segment):
    """Runs BirdNET model inference on a single 3-second segment."""
    # Ensure input matches expected shape
    input_data = np.expand_dims(audio_segment, axis=0).astype(np.float32)

    # Run inference
    interpreter.set_tensor(input_details[0]["index"], input_data)
    interpreter.invoke()

    # Get embeddings instead of species predictions
    embedding_output_index = (
        output_details[0]["index"] - 1
    )  # Typically the previous layer
    embedding = interpreter.get_tensor(embedding_output_index)

    print(f"Embedding shape: {embedding.shape}")  # Debug print

    return embedding.flatten()  # Store embeddings in DB


def store_predictions(segment_id, predictions):
    """Stores predictions in the database."""
    with psycopg.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sensos.predictions (segment_id, scores)
                VALUES (%s, %s);
            """,
                (segment_id, predictions.tolist()),
            )
            conn.commit()


def main():
    """Main processing loop."""
    initialize_schema()  # Ensure database structure exists at startup

    while True:
        print("Checking for new audio segments...")
        unprocessed_audio = get_unprocessed_audio()

        if not unprocessed_audio:
            print("No new audio. Sleeping...")
            time.sleep(10)
            continue

        for segment_id, audio_bytes in unprocessed_audio:
            print(f"Processing segment {segment_id}...")

            # Preprocess audio
            audio_np = process_audio(audio_bytes)

            # Ensure correct segment length
            if len(audio_np) != SEGMENT_SIZE:
                print(
                    f"Skipping {segment_id}: Incorrect segment length ({len(audio_np)})"
                )
                continue

            # Run BirdNET inference
            predictions = predict_species(audio_np)

            # Store results
            store_predictions(segment_id, predictions)

            print(f"Stored predictions for segment {segment_id}")

        print("Processing complete. Sleeping...")
        time.sleep(10)  # Check again after a short delay


if __name__ == "__main__":
    main()
