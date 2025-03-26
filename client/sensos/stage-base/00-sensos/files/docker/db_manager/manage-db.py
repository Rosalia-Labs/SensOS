import os
import time
import argparse
import psycopg
import logging
from psycopg.rows import dict_row
from pathlib import Path
from itertools import groupby
from operator import itemgetter
from io import BytesIO
import soundfile as sf
import numpy as np


# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("db-manager")

# DB connection parameters
DB_PARAMS = {
    "dbname": os.environ.get("POSTGRES_DB", "postgres"),
    "user": os.environ.get("POSTGRES_USER", "postgres"),
    "password": os.environ.get("POSTGRES_PASSWORD", "sensos"),
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", 5432),
}

AUDIO_BASE = Path("/mnt/audio_recordings")

DB_RETRY_DELAY = 5
SLEEP_SECONDS = 60


def connect_with_retry():
    while True:
        try:
            conn = psycopg.connect(**DB_PARAMS, row_factory=dict_row)
            return conn
        except Exception as e:
            logger.warning(f"Waiting for DB connection: {e}")
            time.sleep(DB_RETRY_DELAY)


def initialize_schema(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sensos.merged_segments (
                id SERIAL PRIMARY KEY,
                file_id INTEGER REFERENCES sensos.audio_files(id) ON DELETE CASCADE,
                t_begin TIMESTAMPTZ NOT NULL,
                t_end TIMESTAMPTZ NOT NULL,
                channel INT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sensos.merged_audio (
                segment_id INTEGER PRIMARY KEY REFERENCES sensos.merged_segments(id) ON DELETE CASCADE,
                data BYTEA NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sensos.merged_scores (
                segment_id INTEGER REFERENCES sensos.merged_audio(segment_id) ON DELETE CASCADE,
                species TEXT NOT NULL,
                score FLOAT NOT NULL,
                PRIMARY KEY (segment_id, species)
            );
            """
        )


def find_mergeable_segments(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                bs.species,
                af.id AS file_id,
                ag.channel,
                ag.id AS segment_id,
                ag.t_begin,
                ag.t_end,
                ra.data,
                bs.score
            FROM sensos.birdnet_scores bs
            JOIN sensos.raw_audio ra ON bs.segment_id = ra.segment_id
            JOIN sensos.audio_segments ag ON ra.segment_id = ag.id
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE ra.data IS NOT NULL
            ORDER BY bs.species, af.id, ag.channel, ag.t_begin
            """
        )
        return cur.fetchall()


def group_consecutive_segments(segments):
    grouped = []
    for (species, file_id, channel), group in groupby(
        segments, key=itemgetter("species", "file_id", "channel")
    ):
        run = []
        last_end = None
        for seg in group:
            if last_end is None or seg["t_begin"] == last_end:
                run.append(seg)
                last_end = seg["t_end"]
            else:
                if len(run) > 1:
                    grouped.append((species, file_id, channel, run))
                run = [seg]
                last_end = seg["t_end"]
        if len(run) > 1:
            grouped.append((species, file_id, channel, run))
    return grouped


def merge_and_store(conn, species, file_id, channel, run):
    sample_rate = 48000  # assumed fixed
    segment_length = 3.0  # seconds
    step_size = 1.0  # seconds
    overlap = segment_length - step_size  # 2.0 seconds
    overlap_frames = int(overlap * sample_rate)

    audio = []
    segment_ids = []

    for i, seg in enumerate(run):
        segment_ids.append(seg["segment_id"])
        samples = np.frombuffer(seg["data"], dtype=np.float32)
        if i > 0:
            samples = samples[overlap_frames:]  # drop overlapping head
        audio.append(samples)

    if not audio:
        logger.warning(f"No audio to merge for species {species} — skipping.")
        return

    merged = np.concatenate(audio).astype(np.float32)
    merged_bytes = merged.tobytes()

    t_begin = run[0]["t_begin"]
    t_end = run[-1]["t_end"]

    with conn.cursor() as cur:
        # Create merged segment entry
        cur.execute(
            """
            INSERT INTO sensos.merged_segments (file_id, t_begin, t_end, channel)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """,
            (file_id, t_begin, t_end, channel),
        )
        merged_id = cur.fetchone()[0]

        # Insert audio
        cur.execute(
            """
            INSERT INTO sensos.merged_audio (segment_id, data)
            VALUES (%s, %s)
        """,
            (merged_id, merged_bytes),
        )

        # Compute average score across segments
        avg_score = sum(seg["score"] for seg in run) / len(run)

        # Store merged score
        cur.execute(
            """
            INSERT INTO sensos.merged_scores (segment_id, species, score)
            VALUES (%s, %s, %s)
        """,
            (merged_id, species, avg_score),
        )

        # Nullify raw audio from source segments
        cur.execute(
            "UPDATE sensos.raw_audio SET data = NULL WHERE segment_id = ANY(%s)",
            (segment_ids,),
        )
        conn.commit()
        logger.info(f"Merged {len(run)} segments into merged_segment {merged_id}")


def find_human_vocal_segments(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT bs.segment_id, af.path AS file_path
            FROM sensos.birdnet_scores bs
            JOIN sensos.raw_audio ra ON bs.segment_id = ra.segment_id
            JOIN sensos.audio_segments ag ON ra.segment_id = ag.id
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE bs.species = 'Human vocal'
              AND af.path IS NOT NULL
            """
        )
        return cur.fetchall()


def delete_audio(conn, segment_id: int, file_path: str):
    full_path = AUDIO_BASE / file_path

    # Delete from disk
    if full_path.exists():
        full_path.unlink()
        logger.info(f"Deleted file: {full_path}")
    else:
        logger.warning(f"File not found: {full_path}")

    # Nullify path and raw audio bytes
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sensos.audio_files SET path = NULL WHERE path = %s", (file_path,)
        )
        cur.execute(
            "UPDATE sensos.raw_audio SET data = NULL WHERE segment_id = %s",
            (segment_id,),
        )
        conn.commit()
        logger.info(
            f"Set path = NULL for {file_path} and data = NULL for segment {segment_id}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--delete", action="store_true", help="Delete audio files (not a dry run)"
    )
    args = parser.parse_args()

    mode = "DELETION" if args.delete else "DRY RUN"
    logger.info(f"Running in {mode} mode. Polling every {SLEEP_SECONDS} seconds.")

    while True:
        time.sleep(SLEEP_SECONDS)
        try:
            with connect_with_retry() as conn:
                initialize_schema(conn)
                mergeables = find_mergeable_segments(conn)

                if not mergeables:
                    logger.info("No mergeable segments found.")
                else:
                    runs = group_consecutive_segments(mergeables)
                    for species, file_id, channel, run in runs:
                        merge_and_store(conn, species, file_id, channel, run)

                segments = find_human_vocal_segments(conn)
                if segments:
                    logger.info(
                        f"Found {len(segments)} segments tagged as 'Human vocal'"
                    )
                    for seg in segments:
                        segment_id = seg["segment_id"]
                        file_path = seg["file_path"]
                        logger.info(f"Segment {segment_id}, file: {file_path}")
                        if args.delete:
                            delete_audio(conn, segment_id, file_path)
                else:
                    logger.info("No human vocal segments found.")

        except Exception as e:
            logger.error(f"Error: {e}")


if __name__ == "__main__":
    main()
