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
                label TEXT NOT NULL,
                score FLOAT NOT NULL,
                PRIMARY KEY (segment_id, label)
            );
            """
        )


def find_mergeable_groups(conn):
    with conn.cursor(name="mergeable_cursor") as cur:
        cur.execute(
            """
            SELECT
                bs.label,
                af.id AS file_id,
                ag.channel,
                ag.id AS segment_id,
                ag.t_begin,
                ag.t_end,
                bs.score
            FROM sensos.birdnet_scores bs
            JOIN sensos.audio_segments ag ON bs.segment_id = ag.id
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE EXISTS (
                SELECT 1 FROM sensos.raw_audio ra WHERE ra.segment_id = ag.id AND ra.data IS NOT NULL
            )
            ORDER BY bs.label, af.id, ag.channel, ag.t_begin
            """
        )
        current_group = []
        last_key = None
        for row in cur:
            key = (row["label"], row["file_id"], row["channel"])
            if key != last_key and current_group:
                yield last_key, current_group
                current_group = []
            current_group.append(row)
            last_key = key
        if current_group:
            yield last_key, current_group


def group_consecutive_segments(segments):
    grouped = []
    for (label, file_id, channel), group in groupby(
        segments, key=itemgetter("label", "file_id", "channel")
    ):
        run = []
        last_end = None
        for seg in group:
            if last_end is None or seg["t_begin"] == last_end:
                run.append(seg)
                last_end = seg["t_end"]
            else:
                if len(run) > 1:
                    grouped.append((label, file_id, channel, run))
                run = [seg]
                last_end = seg["t_end"]
        if len(run) > 1:
            grouped.append((label, file_id, channel, run))
    return grouped


def merge_and_store(conn, label, file_id, channel, run):
    segment_ids = [seg["segment_id"] for seg in run]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT segment_id, data FROM sensos.raw_audio WHERE segment_id = ANY(%s)",
            (segment_ids,),
        )
        data_map = {row["segment_id"]: row["data"] for row in cur.fetchall()}

    sample_rate = 48000
    segment_length = 3.0
    step_size = 1.0
    overlap = segment_length - step_size
    overlap_frames = int(overlap * sample_rate)

    audio = []
    for i, seg in enumerate(run):
        raw = data_map.get(seg["segment_id"])
        if raw is None:
            logger.warning(
                f"Missing audio for segment {seg['segment_id']} — skipping run."
            )
            return
        samples = np.frombuffer(raw, dtype=np.float32)
        if i > 0:
            samples = samples[overlap_frames:]
        audio.append(samples)

    if not audio:
        return

    merged = np.concatenate(audio).astype(np.float32)
    merged_bytes = merged.tobytes()

    t_begin = run[0]["t_begin"]
    t_end = run[-1]["t_end"]

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sensos.merged_segments (file_id, t_begin, t_end, channel)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (file_id, t_begin, t_end, channel),
        )
        merged_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO sensos.merged_audio (segment_id, data) VALUES (%s, %s)",
            (merged_id, merged_bytes),
        )

        avg_score = sum(seg["score"] for seg in run) / len(run)
        cur.execute(
            "INSERT INTO sensos.merged_scores (segment_id, label, score) VALUES (%s, %s, %s)",
            (merged_id, label, avg_score),
        )

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
            SELECT bs.segment_id, af.file_path
            FROM sensos.birdnet_scores bs
            JOIN sensos.audio_segments ag ON bs.segment_id = ag.id
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE bs.label = 'Human vocal'
              AND af.file_path IS NOT NULL
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
        return cur.fetchone()["exists"]


def main():
    while True:
        time.sleep(SLEEP_SECONDS)
        try:

            with connect_with_retry() as conn:
                initialize_schema(conn)

                if not table_exists(conn, "birdnet_scores"):
                    logger.info(
                        "Waiting for sensos.birdnet_scores table to be created."
                    )
                    time.sleep(60)
                    continue

                any_found = False
                for (label, file_id, channel), group in find_mergeable_groups(conn):
                    runs = group_consecutive_segments(group)
                    for run in runs:
                        merge_and_store(conn, label, file_id, channel, run)
                        any_found = True
                if not any_found:
                    logger.info("No mergeable segments found.")

                segments = find_human_vocal_segments(conn)
                if segments:
                    logger.info(
                        f"Found {len(segments)} segments tagged as 'Human vocal'"
                    )
                    for seg in segments:
                        segment_id = seg["segment_id"]
                        file_path = seg["file_path"]
                        logger.info(f"Segment {segment_id}, file: {file_path}")
                        delete_audio(conn, segment_id, file_path)
                else:
                    logger.info("No human vocal segments found.")

        except Exception as e:
            logger.error(f"Error: {e}")


if __name__ == "__main__":
    main()
