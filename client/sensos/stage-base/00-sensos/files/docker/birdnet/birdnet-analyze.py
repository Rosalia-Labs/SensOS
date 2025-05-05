#!/usr/bin/env python3

import os
import time
import json
import numpy as np
import psycopg
import tflite_runtime.interpreter as tflite
import librosa
import logging
import sys
import soundfile as sf

from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("audio-analyzer")

# DB connection
DB_PARAMS = (
    f"dbname={os.environ.get('POSTGRES_DB', 'postgres')} "
    f"user={os.environ.get('POSTGRES_USER', 'postgres')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'sensos')} "
    f"host={os.environ.get('DB_HOST', 'sensos-client-database')} "
    f"port={os.environ.get('DB_PORT', '5432')}"
)

# Paths
ROOT = Path("/mnt/audio_recordings")
CATALOGED = ROOT / "cataloged"

# Audio
SAMPLE_RATE = 48000
SEGMENT_DURATION = 3
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION
STEP_SIZE = SAMPLE_RATE  # 1s step

# STFT
N_FFT = 2048
HOP_LENGTH = 512
FULL_SPECTRUM_BINS = 20
BIOACOUSTIC_BINS = 20

# BirdNET
MODEL_PATH = "/model/V2.4/BirdNET_GLOBAL_6K_V2.4_Model_FP32.tflite"
LABELS_PATH = "/model/V2.4/BirdNET_GLOBAL_6K_V2.4_Labels.txt"

interpreter = tflite.Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()


with open(LABELS_PATH, "r") as f:
    LABELS = [
        f"{common} ({sci})" if "_" in line else line.strip()
        for line in f.readlines()
        for sci, common in [line.strip().split("_", 1)]
    ]


def table_exists(conn, table_name):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'sensos' AND table_name = %s
            )
            """,
            (table_name,),
        )
        return cur.fetchone()[0]


def initialize_schema():
    with psycopg.connect(DB_PARAMS) as conn:
        while True:
            if not table_exists(conn, "audio_files"):
                logger.info("Waiting for sensos.audio_files table to be created.")
                time.sleep(60)
            else:
                break

        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute("CREATE SCHEMA IF NOT EXISTS sensos;")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.audio_segments (
                    id SERIAL PRIMARY KEY,
                    file_id INTEGER NOT NULL REFERENCES sensos.audio_files(id) ON DELETE CASCADE,
                    channel INT NOT NULL,
                    start_frame BIGINT NOT NULL,
                    end_frame BIGINT NOT NULL CHECK (end_frame > start_frame),
                    vocal_check BOOLEAN NOT NULL DEFAULT FALSE,
                    UNIQUE (file_id, channel, start_frame)
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS audio_segments_file_id_index
                ON sensos.audio_segments(file_id);
                CREATE INDEX IF NOT EXISTS audio_segments_file_start_index
                ON sensos.audio_segments(file_id, start_frame);
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.sound_statistics (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
                    peak_amplitude FLOAT,
                    rms FLOAT,
                    snr FLOAT
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.full_spectrum (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
                    spectrum JSONB
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.bioacoustic_spectrum (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
                    spectrum JSONB
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_embeddings (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
                    vector vector(1024)
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_scores (
                    segment_id INTEGER REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
                    label TEXT,
                    score FLOAT,
                    PRIMARY KEY (segment_id, label)
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.score_statistics (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
                    hill_number FLOAT,
                    simpson_index FLOAT
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_processed_files (
                    file_id INTEGER PRIMARY KEY REFERENCES sensos.audio_files(id) ON DELETE CASCADE,
                    segment_count INTEGER NOT NULL CHECK (segment_count >= 0),
                    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

            conn.commit()
            logger.info("✅ Schema initialized.")


def get_next_file(cur):
    cur.execute(
        """
        SELECT af.id, af.file_path
        FROM sensos.audio_files af
        WHERE NOT EXISTS (
            SELECT 1 FROM sensos.birdnet_processed_files pf
            WHERE pf.file_id = af.id
        )
        ORDER BY af.cataloged_at
        LIMIT 1;
        """
    )
    return cur.fetchone()


def flat_sigmoid(x, sensitivity=-1, bias=1.0):
    return 1 / (1.0 + np.exp(sensitivity * np.clip((x + (bias - 1.0) * 10.0), -20, 20)))


def compute_audio_features(audio):
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio**2)))
    snr = float(20 * np.log10(peak / rms)) if rms > 1e-12 else 0.0
    return peak, rms, snr


def get_freq_bins(min_f, max_f, bins):
    return np.logspace(np.log10(min_f), np.log10(max_f), bins + 1)


def compute_binned_spectrum(audio, min_freq, max_freq, bins):
    S = np.abs(librosa.stft(audio, n_fft=N_FFT, hop_length=HOP_LENGTH)) ** 2
    freqs = librosa.fft_frequencies(sr=SAMPLE_RATE, n_fft=N_FFT)
    bin_edges = get_freq_bins(min_freq, max_freq, bins)
    return librosa.power_to_db(
        [
            np.sum(S[(freqs >= bin_edges[i]) & (freqs < bin_edges[i + 1])])
            for i in range(bins)
        ],
        ref=1.0,
    ).tolist()


# This mirrors libsoundfile c code
def scale_by_max_value(audio: np.ndarray) -> np.ndarray:
    max_val = np.max(np.abs(audio))
    if max_val == 0:
        return np.zeros_like(audio, dtype=np.float32)

    scale = max_val * (32768.0 / 32767.0)
    return (audio / scale).astype(np.float32)


def invoke_birdnet(audio):
    input_data = np.expand_dims(audio, axis=0).astype(np.float32)
    interpreter.set_tensor(input_details[0]["index"], input_data)
    interpreter.invoke()
    scores = interpreter.get_tensor(output_details[0]["index"])
    embedding = interpreter.get_tensor(output_details[0]["index"] - 1)
    scores_flat = flat_sigmoid(scores.flatten())
    embedding_flat = embedding.flatten()

    total = np.sum(scores_flat)
    probs = scores_flat / total if total > 0 else np.zeros_like(scores_flat)
    entropy = -np.sum(probs[probs > 0] * np.log2(probs[probs > 0]))
    return (
        embedding_flat,
        {LABELS[i]: scores_flat[i] for i in np.argsort(scores_flat)[-5:][::-1]},
        float(2**entropy),
        float(np.sum(probs**2)),
    )


def fetch_and_verify_metadata(cur, file_id):
    """
    Fetch metadata from the database for the given file_id,
    verify it against actual file contents, and return:
      - full absolute Path to the file
      - metadata dict with fields: channels, sample_rate, frames, format, subtype

    Raises ValueError if verification fails or file is missing.
    """
    cur.execute(
        """
        SELECT file_path, channels, sample_rate, frames, format, subtype
        FROM sensos.audio_files
        WHERE id = %s;
        """,
        (file_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"No file entry found for id {file_id}")

    file_path, channels, sample_rate, frames, fmt, subtype = row
    path = CATALOGED / Path(file_path).relative_to("cataloged")
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    info = sf.info(path)
    if (
        info.channels != channels
        or info.samplerate != sample_rate
        or info.frames != frames
        or info.format != fmt
        or info.subtype != subtype
    ):
        raise ValueError(f"Metadata mismatch for file {file_path}")

    return path, {
        "channels": info.channels,
        "sample_rate": info.samplerate,
        "frames": info.frames,
        "format": info.format,
        "subtype": info.subtype,
    }


def get_file_and_metadata(cur):
    file_entry = get_next_file(cur)
    if not file_entry:
        return None
    file_id, file_path = file_entry
    abs_path, meta = fetch_and_verify_metadata(cur, file_id)
    return file_id, file_path, abs_path, meta


def process_file(file_id, file_path, abs_path, meta):
    logger.info(
        f"Processing {file_path} ({meta['channels']} ch, {meta['frames']/meta['sample_rate']:.1f} s)"
    )

    with sf.SoundFile(abs_path.as_posix(), "r") as f:
        if f.channels != meta["channels"] or f.samplerate != SAMPLE_RATE:
            logger.warning("Unexpected file format")

        with psycopg.connect(DB_PARAMS) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    count = analyze_segments(f, cur, file_id, meta["channels"])
                    cur.execute(
                        """
                        INSERT INTO sensos.birdnet_processed_files (file_id, segment_count)
                        VALUES (%s, %s);
                        """,
                        (file_id, count),
                    )


def analyze_segments(f, cur, file_id, channels) -> int:
    segment_count = 0

    for start in range(0, int(f.frames) - SEGMENT_SIZE + 1, STEP_SIZE):
        f.seek(start)
        raw_audio_all = f.read(SEGMENT_SIZE, dtype="int32", always_2d=True)

        for ch in range(channels):
            raw_audio = raw_audio_all[:, ch]
            if len(raw_audio) != SEGMENT_SIZE:
                continue

            segment_id = insert_segment(cur, file_id, ch, start, start + SEGMENT_SIZE)
            analyze_and_store_features(cur, segment_id, raw_audio)
            segment_count += 1

    return segment_count


def insert_segment(cur, file_id, ch, start, end):
    cur.execute(
        """
        INSERT INTO sensos.audio_segments (file_id, channel, start_frame, end_frame)
        VALUES (%s, %s, %s, %s)
        RETURNING id;
        """,
        (file_id, ch, start, end),
    )
    return cur.fetchone()[0]


def analyze_and_store_features(cur, segment_id, raw_audio):
    peak, rms, snr = compute_audio_features(raw_audio)
    float_audio = raw_audio.astype(np.float32)
    full_spec = compute_binned_spectrum(
        float_audio, 50, SAMPLE_RATE // 2, FULL_SPECTRUM_BINS
    )
    bio_spec = compute_binned_spectrum(float_audio, 1000, 8000, BIOACOUSTIC_BINS)
    normalized_audio = scale_by_max_value(float_audio)
    embedding, top_scores, hill, simpson = invoke_birdnet(normalized_audio)

    cur.execute(
        "INSERT INTO sensos.sound_statistics (segment_id, peak_amplitude, rms, snr) VALUES (%s, %s, %s, %s);",
        (segment_id, peak, rms, snr),
    )
    cur.execute(
        "INSERT INTO sensos.full_spectrum (segment_id, spectrum) VALUES (%s, %s);",
        (segment_id, json.dumps(full_spec)),
    )
    cur.execute(
        "INSERT INTO sensos.bioacoustic_spectrum (segment_id, spectrum) VALUES (%s, %s);",
        (segment_id, json.dumps(bio_spec)),
    )
    cur.execute(
        "INSERT INTO sensos.birdnet_embeddings (segment_id, vector) VALUES (%s, %s);",
        (segment_id, embedding.tolist()),
    )
    for label, score in top_scores.items():
        cur.execute(
            "INSERT INTO sensos.birdnet_scores (segment_id, label, score) VALUES (%s, %s, %s);",
            (segment_id, label, score),
        )
    cur.execute(
        "INSERT INTO sensos.score_statistics (segment_id, hill_number, simpson_index) VALUES (%s, %s, %s);",
        (segment_id, hill, simpson),
    )


def main():
    initialize_schema()

    while True:
        try:
            with psycopg.connect(DB_PARAMS) as conn:
                with conn.cursor() as cur:
                    result = get_file_and_metadata(cur)
                    if result is None:
                        logger.info("No unprocessed files found. Sleeping 60s...")
                        time.sleep(60)
                        continue
                    file_id, file_path, abs_path, meta = result

            process_file(file_id, file_path, abs_path, meta)

        except Exception as e:
            logger.exception("❌ Failed to process file. Rolled back.")
            time.sleep(10)


if __name__ == "__main__":
    main()
