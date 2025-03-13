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

LABELS_PATH = "/app/model/BirdNET_GLOBAL_6K_V2.4_Labels.txt"
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

        time.sleep(5)  # Wait before checking again


def initialize_schema():
    """Ensures the required database schema and tables exist."""
    wait_for_schema()  # Ensure 'sensos' schema exists before proceeding

    with psycopg.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            # Table for embeddings
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.embeddings (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.raw_audio(segment_id) ON DELETE CASCADE,
                    vector vector(1024) NOT NULL
                );
                """
            )

            # Table for species predictions
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.predictions (
                    segment_id INTEGER REFERENCES sensos.raw_audio(segment_id) ON DELETE CASCADE,
                    species TEXT NOT NULL,
                    confidence FLOAT NOT NULL,
                    PRIMARY KEY (segment_id, species)
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
                WHERE segment_id NOT IN (SELECT segment_id FROM sensos.embeddings);
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


def project_data(audio_segment):
    """Runs BirdNET model inference on a single 3-second segment and extracts embeddings and species predictions."""
    # Ensure input matches expected shape
    input_data = np.expand_dims(audio_segment, axis=0).astype(np.float32)

    # Run inference
    interpreter.set_tensor(input_details[0]["index"], input_data)
    interpreter.invoke()

    # Get the output tensor indices:
    # Assume that the current output_details[0] holds the species confidences (shape (1, 6522))
    # and that the embeddings are stored in the tensor just before it (index - 1, shape (1, 1024))
    species_output_index = output_details[0]["index"]
    embedding_output_index = species_output_index - 1

    # Retrieve outputs
    species = interpreter.get_tensor(species_output_index)
    embedding = interpreter.get_tensor(embedding_output_index)

    print(f"Species shape: {species.shape}")  # Expected: (1, 6522)
    print(f"Embedding shape: {embedding.shape}")  # Expected: (1, 1024)

    # Flatten the embedding to 1D
    embedding_flat = embedding.flatten()

    # Flatten species and align with labels
    species_flat = species.flatten()
    species_predictions = {LABELS[i]: species_flat[i] for i in range(len(species_flat))}

    return embedding_flat, species_predictions


def store_results(segment_id, embeddings, predictions, top_n=5):
    """Stores embeddings (if available) and top-N species predictions in the database."""

    with psycopg.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            # Store embeddings only if they exist
            if embeddings is not None:
                cur.execute(
                    """
                    INSERT INTO sensos.embeddings (segment_id, vector)
                    VALUES (%s, %s);
                    """,
                    (segment_id, embeddings.tolist()),
                )

            # Store top-N species
            top_species = sorted(predictions.items(), key=lambda x: x[1], reverse=True)[
                :top_n
            ]
            for species, confidence in top_species:
                cur.execute(
                    """
                    INSERT INTO sensos.predictions (segment_id, species, confidence)
                    VALUES (%s, %s, %s);
                    """,
                    (segment_id, species, confidence),
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
            embeddings, species = project_data(audio_np)

            # Store results
            store_results(segment_id, embeddings, species)

            print(f"Stored embeddings for segment {segment_id}")

        print("Processing complete. Sleeping...")
        time.sleep(10)  # Check again after a short delay


if __name__ == "__main__":
    main()
