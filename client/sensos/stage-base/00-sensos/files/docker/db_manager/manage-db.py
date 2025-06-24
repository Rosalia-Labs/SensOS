import os
import time
import psycopg
import logging
from psycopg.rows import dict_row
from pathlib import Path
from typing import Optional, Dict, Any, Set, List
import numpy as np
import soundfile as sf

# --- Config & Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("db-manager")

DB_PARAMS = {
    "dbname": os.environ.get("POSTGRES_DB", "postgres"),
    "user": os.environ.get("POSTGRES_USER", "postgres"),
    "password": os.environ.get("POSTGRES_PASSWORD", "sensos"),
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", 5432),
}
AUDIO_BASE = Path("/audio_recordings")
BIRDNET_SCORE_THRESHOLD = float(os.environ.get("BIRDNET_SCORE_THRESHOLD", 0.1))


def connect_with_retry() -> psycopg.Connection:
    """Try to connect to Postgres with retry."""
    while True:
        try:
            conn = psycopg.connect(**DB_PARAMS, row_factory=dict_row)
            return conn
        except Exception as e:
            logger.warning(f"Waiting for DB connection: {e}")
            time.sleep(5)


def table_exists(conn: psycopg.Connection, table_name: str) -> bool:
    """Check if table exists in sensos schema."""
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


def get_next_unchecked_segments(
    conn: psycopg.Connection, batch_size: int = 100
) -> List[Dict[str, Any]]:
    """Fetch the next batch of unchecked segments."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ag.id AS segment_id, af.file_path, af.id AS file_id,
                   ag.channel, ag.start_frame, ag.end_frame
            FROM sensos.audio_segments ag
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE NOT ag.checked
              AND af.deleted = FALSE
              AND af.file_path LIKE '%.wav'
            ORDER BY ag.start_frame
            LIMIT %s
            """,
            (batch_size,),
        )
        return cur.fetchall()


def check_segment_for_erasure(conn: psycopg.Connection, segment_id: int) -> bool:
    """
    Returns True if the segment should be zeroed, False otherwise.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT label, score FROM sensos.birdnet_scores
            WHERE segment_id = %s
            """,
            (segment_id,),
        )
        scores = cur.fetchall()
        if not scores:
            logger.warning(
                f"Segment {segment_id} has no BirdNET scores; will NOT zero by default."
            )
            return False  # Or change to True if that's your intent!
        for row in scores:
            if (
                row["label"].lower().startswith("human vocal")
                and row["score"] >= BIRDNET_SCORE_THRESHOLD
            ):
                logger.info(
                    f"Segment {segment_id} flagged for zeroing: Human vocal, score={row['score']}"
                )
                return True
        if all(row["score"] < BIRDNET_SCORE_THRESHOLD for row in scores):
            logger.info(
                f"Segment {segment_id} flagged for zeroing: all BirdNET scores below threshold"
            )
            return True
        return False


def mark_segment_checked(conn: psycopg.Connection, seg: Dict[str, Any]) -> None:
    """Mark segment as checked."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sensos.audio_segments SET checked = TRUE WHERE id = %s",
            (seg["segment_id"],),
        )
        conn.commit()


def mark_segment_zeroed(conn: psycopg.Connection, seg: Dict[str, Any]) -> None:
    """Mark segment as zeroed (erased)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sensos.audio_segments SET zeroed = TRUE WHERE id = %s",
            (seg["segment_id"],),
        )
        conn.commit()


def overwrite_segment_with_zeros(seg: Dict[str, Any]) -> bool:
    """Overwrite the segment with zeros."""
    frame_count = seg["end_frame"] - seg["start_frame"]
    full_path = AUDIO_BASE / seg["file_path"]
    if not full_path.exists():
        logger.warning(f"Missing file on disk: {full_path}")
        return False

    try:
        with sf.SoundFile(full_path, mode="r+") as f:
            f.seek(seg["start_frame"])
            zeros = np.zeros((frame_count, f.channels), dtype="float32")
            f.write(zeros)
            f.flush()
        logger.info(
            f'Wrote zeros to {seg["file_path"]} at frame {seg["start_frame"]} for {frame_count} frames'
        )
        return True
    except Exception as e:
        logger.error(f'Failed to zero segment in {seg["file_path"]}: {e}')
        return False


def is_file_fully_zeroed(conn: psycopg.Connection, file_id: int) -> bool:
    """Return True if all segments for file are zeroed."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT BOOL_AND(zeroed) AS all_zeroed
            FROM sensos.audio_segments
            WHERE file_id = %s
            """,
            (file_id,),
        )
        result = cur.fetchone()
        return result and result["all_zeroed"] is True


def all_segments_checked(conn: psycopg.Connection, file_id: int) -> bool:
    """Return True if all segments for file are checked."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT BOOL_AND(checked) AS all_checked
            FROM sensos.audio_segments
            WHERE file_id = %s
            """,
            (file_id,),
        )
        result = cur.fetchone()
        return result and result["all_checked"] is True


def mark_file_deleted(conn: psycopg.Connection, file_id: int) -> None:
    """Mark file as deleted in DB."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sensos.audio_files SET deleted = TRUE, deleted_at = NOW() WHERE id = %s",
            (file_id,),
        )
        conn.commit()


def delete_audio_file_from_disk(seg: Dict[str, Any]) -> None:
    """Delete audio file from disk."""
    file_path = AUDIO_BASE / seg["file_path"]
    if file_path.exists():
        try:
            file_path.unlink()
            logger.info(f"Deleted fully zeroed file: {file_path}")
        except Exception as e:
            logger.error(f"Failed to delete zeroed file {file_path}: {e}")


def compress_file_if_done(
    conn: psycopg.Connection, file_id: int, file_path: str
) -> None:
    """Compress .wav to .flac and update DB, if all segments checked."""
    wav_path = AUDIO_BASE / file_path
    flac_path = wav_path.with_suffix(".flac")
    if not wav_path.exists() or wav_path.suffix.lower() != ".wav":
        return

    if all_segments_checked(conn, file_id):
        try:
            data, sr = sf.read(wav_path)
            sf.write(flac_path, data, sr, format="FLAC")
            wav_path.unlink()
            new_rel_path = flac_path.relative_to(AUDIO_BASE).as_posix()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sensos.audio_files SET file_path = %s WHERE id = %s",
                    (new_rel_path, file_id),
                )
                conn.commit()
            logger.info(f"Compressed to FLAC and updated DB: {new_rel_path}")
        except Exception as e:
            logger.error(f"Failed to compress {wav_path} to FLAC: {e}")


def handle_fully_zeroed_file(conn: psycopg.Connection, seg: Dict[str, Any]) -> None:
    """Delete file from disk and mark as deleted in DB if all segments zeroed."""
    if is_file_fully_zeroed(conn, seg["file_id"]):
        delete_audio_file_from_disk(seg)
        mark_file_deleted(conn, seg["file_id"])


def handle_compression_for_files(conn: psycopg.Connection, file_ids: Set[int]) -> None:
    """Try to compress to FLAC for each affected file."""
    for file_id in file_ids:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_path FROM sensos.audio_files WHERE id = %s AND deleted = FALSE",
                (file_id,),
            )
            row = cur.fetchone()
            if row:
                compress_file_if_done(conn, file_id, row["file_path"])


def process_segment(conn: psycopg.Connection, seg: Dict[str, Any]) -> Optional[int]:
    """
    Check, mark, erase as needed for one segment.
    Returns file_id if erased (and file needs checking for zero/deletion).
    """
    erase = check_segment_for_erasure(conn, seg["segment_id"])
    mark_segment_checked(conn, seg)
    if erase:
        success = overwrite_segment_with_zeros(seg)
        if success:
            mark_segment_zeroed(conn, seg)
            logger.info(f"Zeroed segment {seg['segment_id']}.")
            handle_fully_zeroed_file(conn, seg)
            return seg["file_id"]
        else:
            logger.warning(f"Failed to zero segment {seg['segment_id']}.")
    return None


def wait_for_birdnet_table(conn: psycopg.Connection) -> None:
    """Wait for BirdNET table to exist before processing."""
    while not table_exists(conn, "birdnet_scores"):
        logger.info("Waiting for sensos.birdnet_scores table to be created.")
        time.sleep(60)


def main_loop() -> None:
    """Main infinite processing loop."""
    while True:
        try:
            with connect_with_retry() as conn:
                wait_for_birdnet_table(conn)
                segments = get_next_unchecked_segments(conn, batch_size=100)
                if not segments:
                    logger.info("No unchecked segments to process.")
                    time.sleep(60)
                    continue

                touched_file_ids: Set[int] = set()
                for seg in segments:
                    file_id = process_segment(conn, seg)
                    if file_id:
                        touched_file_ids.add(file_id)

                handle_compression_for_files(conn, touched_file_ids)
        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(60)


def main() -> None:
    time.sleep(60)
    main_loop()


if __name__ == "__main__":
    main()
