#!/usr/bin/env python3

import os
import time
import json
import logging
import numpy as np
import psycopg
import soundfile as sf
import tflite_runtime.interpreter as tflite
from datetime import date
import shutil

from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from sound_utils import (
    load_birdnet_model,
    BirdNETModel,
    compute_audio_features,
    compute_binned_spectrum,
    scale_by_max_value,
    invoke_birdnet_with_location,
)

try:
    shutil.rmtree("/root/.cache/numba")
except Exception:
    pass


def safe_float_env(key: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(key, default) or default)
    except ValueError:
        return default


latitude = safe_float_env("LATITUDE")
longitude = safe_float_env("LONGITUDE")

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("audio-analyzer")

# DB connection
DB_PARAMS: str = (
    f"dbname={os.environ.get('POSTGRES_DB', 'postgres')} "
    f"user={os.environ.get('POSTGRES_USER', 'postgres')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'sensos')} "
    f"host={os.environ.get('DB_HOST', 'sensos-client-database')} "
    f"port={os.environ.get('DB_PORT', '5432')}"
)

# Paths
ROOT: Path = Path("/audio_recordings")
CATALOGED: Path = ROOT / "cataloged"

# Audio constants
SAMPLE_RATE: int = 48000
SEGMENT_DURATION: int = 3
SEGMENT_SIZE: int = SAMPLE_RATE * SEGMENT_DURATION
STEP_SIZE: int = SAMPLE_RATE  # 1s step

# Spectrogram constants
N_FFT: int = 2048
HOP_LENGTH: int = 512
FULL_SPECTRUM_BINS: int = 20
BIOACOUSTIC_BINS: int = 20

# BirdNET model and labels
MODEL_PATH: str = "/model/BirdNET_v2.4_tflite/audio-model.tflite"
LABELS_PATH: str = "/model/BirdNET_v2.4_tflite/labels/en_us.txt"

birdnet_model: BirdNETModel = load_birdnet_model(MODEL_PATH, LABELS_PATH)

META_MODEL_PATH: str = "/model/BirdNET_v2.4_tflite/meta-model.tflite"

birdnet_meta_model: BirdNETModel = load_birdnet_model(META_MODEL_PATH, LABELS_PATH)


def table_exists(conn: psycopg.Connection, table_name: str) -> bool:
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


def initialize_schema() -> None:
    with psycopg.connect(DB_PARAMS) as conn:
        while not table_exists(conn, "audio_files"):
            logger.info("Waiting for sensos.audio_files table to be created.")
            time.sleep(60)

        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute("CREATE SCHEMA IF NOT EXISTS sensos;")

            # 1. audio_segments
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.audio_segments (
                    id SERIAL PRIMARY KEY
                );
            """
            )
            cur.execute(
                """
                ALTER TABLE sensos.audio_segments
                    ADD COLUMN IF NOT EXISTS file_id INTEGER REFERENCES sensos.audio_files(id) ON DELETE CASCADE,
                    ADD COLUMN IF NOT EXISTS channel INT,
                    ADD COLUMN IF NOT EXISTS start_frame BIGINT,
                    ADD COLUMN IF NOT EXISTS end_frame BIGINT CHECK (end_frame > start_frame),
                    ADD COLUMN IF NOT EXISTS zeroed BOOLEAN DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW(),
                    ADD COLUMN IF NOT EXISTS processed BOOLEAN DEFAULT FALSE;
            """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS audio_segments_unique_idx
                ON sensos.audio_segments(file_id, channel, start_frame);
            """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS audio_segments_file_id_index
                ON sensos.audio_segments(file_id);
            """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS audio_segments_file_start_index
                ON sensos.audio_segments(file_id, start_frame);
            """
            )

            # 2. sound_statistics
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.sound_statistics (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE
                );
            """
            )
            cur.execute(
                """
                ALTER TABLE sensos.sound_statistics
                    ADD COLUMN IF NOT EXISTS peak_amplitude FLOAT,
                    ADD COLUMN IF NOT EXISTS rms FLOAT,
                    ADD COLUMN IF NOT EXISTS snr FLOAT;
            """
            )

            # 3. full_spectrum
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.full_spectrum (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE
                );
            """
            )
            cur.execute(
                """
                ALTER TABLE sensos.full_spectrum
                    ADD COLUMN IF NOT EXISTS spectrum JSONB;
            """
            )

            # 4. bioacoustic_spectrum
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.bioacoustic_spectrum (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE
                );
            """
            )
            cur.execute(
                """
                ALTER TABLE sensos.bioacoustic_spectrum
                    ADD COLUMN IF NOT EXISTS spectrum JSONB;
            """
            )

            # 5. birdnet_embeddings
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_embeddings (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE
                );
            """
            )
            cur.execute(
                """
                ALTER TABLE sensos.birdnet_embeddings
                    ADD COLUMN IF NOT EXISTS vector vector(1024);
            """
            )

            # 6. birdnet_scores
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_scores (
                    segment_id INTEGER REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    score FLOAT NOT NULL,
                    likely FLOAT,
                    PRIMARY KEY (segment_id, label)
                );
            """
            )

            # 7. score_statistics
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.score_statistics (
                    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE
                );
            """
            )
            cur.execute(
                """
                ALTER TABLE sensos.score_statistics
                    ADD COLUMN IF NOT EXISTS hill_number FLOAT,
                    ADD COLUMN IF NOT EXISTS simpson_index FLOAT;
            """
            )

            # 8. birdnet_processed_files
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.birdnet_processed_files (
                    file_id INTEGER PRIMARY KEY REFERENCES sensos.audio_files(id) ON DELETE CASCADE
                );
            """
            )
            cur.execute(
                """
                ALTER TABLE sensos.birdnet_processed_files
                    ADD COLUMN IF NOT EXISTS segment_count INTEGER CHECK (segment_count >= 0),
                    ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ DEFAULT NOW();
            """
            )

            conn.commit()
            logger.info("✅ Schema initialized.")


def get_next_file(cur: psycopg.Cursor) -> Optional[Tuple[int, str]]:
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


def fetch_metadata(
    cur: psycopg.Cursor, file_id: int
) -> Optional[Tuple[Path, Dict[str, Any]]]:
    cur.execute("SELECT file_path FROM sensos.audio_files WHERE id = %s;", (file_id,))
    row = cur.fetchone()
    if row is None:
        return None

    (file_path,) = row
    path = CATALOGED / Path(file_path).relative_to("cataloged")

    info = sf.info(path)
    return path, {
        "channels": info.channels,
        "sample_rate": info.samplerate,
        "frames": info.frames,
        "format": info.format,
        "subtype": info.subtype,
    }


def get_file_and_metadata(
    cur: psycopg.Cursor,
) -> Optional[Tuple[int, str, Path, Dict[str, Any]]]:
    file_entry = get_next_file(cur)
    if not file_entry:
        return None

    file_id, file_path = file_entry
    result = fetch_metadata(cur, file_id)
    if result is None:
        return None

    abs_path, meta = result
    return file_id, file_path, abs_path, meta


def process_file(
    cur: psycopg.Cursor, file_info: Tuple[int, str, Path, Dict[str, Any]]
) -> None:
    file_id, file_path, abs_path, meta = file_info
    logger.info(
        f"Processing {file_path} ({meta['channels']} ch, {meta['frames']/meta['sample_rate']:.1f} s)"
    )
    with sf.SoundFile(abs_path.as_posix(), "r") as f:
        count = analyze_segments(f, cur, file_id, meta["channels"])
        cur.execute(
            "INSERT INTO sensos.birdnet_processed_files (file_id, segment_count) VALUES (%s, %s);",
            (file_id, count),
        )


def analyze_segments(
    f: sf.SoundFile, cur: psycopg.Cursor, file_id: int, channels: int
) -> int:
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


def insert_segment(
    cur: psycopg.Cursor, file_id: int, ch: int, start: int, end: int
) -> int:
    cur.execute(
        "INSERT INTO sensos.audio_segments (file_id, channel, start_frame, end_frame) VALUES (%s, %s, %s, %s) RETURNING id;",
        (file_id, ch, start, end),
    )
    return cur.fetchone()[0]


def get_segment_date(cur: "psycopg.Cursor", segment_id: int) -> date:
    """
    Fetches the recording date for the given audio segment.
    Assumes every segment has a non-null capture_timestamp.

    Args:
        cur: Database cursor.
        segment_id: ID of the segment in sensos.audio_segments.

    Returns:
        Observation date as a datetime.date.

    Raises:
        ValueError: If the segment is not found.
    """
    cur.execute(
        """
        SELECT f.capture_timestamp
        FROM sensos.audio_segments s
        JOIN sensos.audio_files f ON s.file_id = f.id
        WHERE s.id = %s
        """,
        (segment_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"Segment {segment_id} not found or missing audio_files row.")

    return row[0].date()


def analyze_and_store_features(
    cur: psycopg.Cursor, segment_id: int, raw_audio: np.ndarray
) -> None:
    peak, rms, snr = compute_audio_features(raw_audio)
    float_audio = raw_audio.astype(np.float32)
    full_spec = compute_binned_spectrum(
        float_audio,
        SAMPLE_RATE,
        N_FFT,
        HOP_LENGTH,
        50,
        SAMPLE_RATE // 2,
        FULL_SPECTRUM_BINS,
    )
    bio_spec = compute_binned_spectrum(
        float_audio, SAMPLE_RATE, N_FFT, HOP_LENGTH, 1000, 8000, BIOACOUSTIC_BINS
    )
    normalized_audio = scale_by_max_value(float_audio)
    obs_date = get_segment_date(cur, segment_id)
    embedding, top_scores, hill, simpson = invoke_birdnet_with_location(
        normalized_audio,
        birdnet_model,
        birdnet_meta_model,
        latitude,
        longitude,
        obs_date,
    )
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
    for label, (score, likely) in top_scores.items():
        cur.execute(
            "INSERT INTO sensos.birdnet_scores (segment_id, label, score, likely) VALUES (%s, %s, %s, %s);",
            (segment_id, label, score, likely),
        )
    cur.execute(
        "INSERT INTO sensos.score_statistics (segment_id, hill_number, simpson_index) VALUES (%s, %s, %s);",
        (segment_id, hill, simpson),
    )


def is_valid_metadata(file_info: Tuple[int, str, Path, Dict[str, Any]]) -> bool:
    file_path = None
    try:
        _, file_path, abs_path, meta = file_info
        info = sf.info(abs_path)
        return (
            info.channels == meta["channels"]
            and info.samplerate == meta["sample_rate"]
            and info.frames == meta["frames"]
            and info.format == meta["format"]
            and info.subtype == meta["subtype"]
        )
    except Exception as e:
        logger.warning(f"Metadata check failed for {file_path}: {e}")
        return False


def main() -> None:
    initialize_schema()
    while True:
        try:
            with psycopg.connect(DB_PARAMS) as conn:
                with conn.cursor() as cur:
                    file_info = get_file_and_metadata(cur)

                    if file_info is None:
                        logger.info("No unprocessed files found. Sleeping 60s...")
                        time.sleep(60)
                        continue

                    file_id, file_path, abs_path, meta = file_info

                    if not abs_path.exists():
                        logger.warning(f"File missing from disk: {abs_path}")
                        cur.execute(
                            "UPDATE sensos.audio_files SET deleted = TRUE WHERE id = %s",
                            (file_id,),
                        )
                        conn.commit()
                        continue

                    if is_valid_metadata(file_info):
                        process_file(cur, file_info)
                    else:
                        try:
                            abs_path.unlink(missing_ok=True)
                            cur.execute(
                                "DELETE FROM sensos.audio_files WHERE id = %s",
                                (file_id,),
                            )
                            logger.warning(
                                f"Deleted DB record for {file_path} due to invalid metadata"
                            )
                            conn.commit()
                            logger.warning(
                                f"Marked invalid file as deleted: {file_path}"
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to mark invalid file deleted: {file_path} — {e}"
                            )
                            conn.rollback()

        except Exception as e:
            logger.exception("❌ Failed to process file. Rolled back.")
            time.sleep(10)


if __name__ == "__main__":
    main()
