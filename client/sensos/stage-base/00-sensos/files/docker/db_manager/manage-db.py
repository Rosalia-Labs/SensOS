import os
import time
import psycopg
import logging
from psycopg.rows import dict_row
from pathlib import Path
import numpy as np
import soundfile as sf

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


def connect_with_retry():
    while True:
        try:
            conn = psycopg.connect(**DB_PARAMS, row_factory=dict_row)
            return conn
        except Exception as e:
            logger.warning(f"Waiting for DB connection: {e}")
            time.sleep(5)


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


def overwrite_segment_with_zeros(
    file_path: str, start_frame: int, num_frames: int, channel: int
):
    full_path = AUDIO_BASE / file_path
    if not full_path.exists():
        logger.warning(f"Missing file on disk: {full_path}")
        return False

    try:
        with sf.SoundFile(full_path, mode="r+") as f:
            if f.channels <= channel:
                logger.warning(f"Channel {channel} out of bounds for file {file_path}")
                return False
            f.seek(start_frame)
            zeros = np.zeros((num_frames, f.channels), dtype="float32")
            f.write(zeros)
            f.flush()
        logger.info(
            f"Wrote zeros to {file_path} at frame {start_frame} for {num_frames} frames"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to zero segment in {file_path}: {e}")
        return False


def zero_human_vocal_segments(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ag.id AS segment_id, af.file_path, ag.channel, ag.start_frame, ag.duration_s
            FROM sensos.birdnet_scores bs
            JOIN sensos.audio_segments ag ON bs.segment_id = ag.id
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE bs.label ILIKE '%Human vocal%'
                AND af.file_path IS NOT NULL
                AND NOT ag.zeroed
            LIMIT 1
            """
        )
        seg = cur.fetchone()

    if not seg:
        logger.info("No more human vocal segments to zero.")
        return False

    frame_count = int(seg["duration_s"] * 48000)
    success = overwrite_segment_with_zeros(
        seg["file_path"], seg["start_frame"], frame_count, seg["channel"]
    )

    if success:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sensos.audio_segments SET zeroed = TRUE WHERE id = %s",
                (seg["segment_id"],),
            )
            conn.commit()
    return True


def main():
    time.sleep(60)
    while True:
        try:
            with connect_with_retry() as conn:
                if not table_exists(conn, "birdnet_scores"):
                    logger.info(
                        "Waiting for sensos.birdnet_scores table to be created."
                    )
                    time.sleep(60)
                    continue
                segment_found = zero_human_vocal_segments(conn)
                if not segment_found:
                    time.sleep(60)

        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
