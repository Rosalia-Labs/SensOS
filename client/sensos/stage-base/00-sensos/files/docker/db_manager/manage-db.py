import os
import time
import shutil
import psycopg
import logging
from psycopg.rows import dict_row
from pathlib import Path
from typing import Optional, Dict, Any, Set, List
import numpy as np
import soundfile as sf
import traceback


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
ENABLE_COMPRESSION = os.environ.get("SENSOS_ENABLE_COMPRESSION", "0").lower() in (
    "1",
    "true",
    "yes",
)


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
        row = cur.fetchone()
        return row["exists"] if row else False


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
              AND af.file_path LIKE '%%.wav'
            ORDER BY ag.start_frame
            LIMIT %s
            """,
            (batch_size,),
        )
        return cur.fetchall()


def get_birdnet_scores(
    conn: psycopg.Connection, segment_id: int
) -> List[Dict[str, Any]]:
    """Fetch BirdNET scores for a segment."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT label, score FROM sensos.birdnet_scores
            WHERE segment_id = %s
            """,
            (segment_id,),
        )
        return cur.fetchall()


def should_zero_segment(scores: List[Dict[str, Any]]) -> bool:
    """
    Returns True if the segment should be zeroed, False otherwise.
    Expects a non-empty list of BirdNET scores.
    """
    for row in scores:
        if "human" in row["label"].lower() and row["score"] >= BIRDNET_SCORE_THRESHOLD:
            logger.info(
                f"Flagged for zeroing: label='{row['label']}', score={row['score']}"
            )
            return True

    if all(row["score"] < BIRDNET_SCORE_THRESHOLD for row in scores):
        logger.info("Flagged for zeroing: all BirdNET scores below threshold")
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
    full_path = AUDIO_BASE / seg["file_path"]

    frame_count = seg["end_frame"] - seg["start_frame"]
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


def zero_segment(conn, seg: Dict[str, Any]) -> Optional[int]:
    if overwrite_segment_with_zeros(seg):
        mark_segment_zeroed(conn, seg)
        logger.info(f"Zeroed segment {seg['segment_id']}.")
        handle_fully_zeroed_file(conn, seg)
        return seg["file_id"]
    else:
        logger.warning(f"Segment {seg['segment_id']} not zeroed.")
        return None


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
    if not ENABLE_COMPRESSION:
        logger.info("Compression is disabled by config. Skipping FLAC compression.")
        return
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
    Inspect one segment:
    - Only mark as checked if scores exist.
    - If zeroing is needed, zero the segment and mark as zeroed.
    - Return file_id if erased, else None.
    """
    if seg.get("zeroed"):
        mark_segment_checked(conn, seg)
        logger.warning(
            f"Segment {seg['segment_id']} is zeroed but reports not checked."
        )
        return None
    scores = get_birdnet_scores(conn, seg["segment_id"])
    if not scores:
        logger.warning(
            f"Segment {seg['segment_id']} has no BirdNET scores; skipping for now."
        )
        return None
    mark_segment_checked(conn, seg)
    if should_zero_segment(scores):
        return zero_segment(conn, seg)
    return None


def wait_for_birdnet_table(conn: psycopg.Connection) -> None:
    """Wait for BirdNET table to exist before processing."""
    while not table_exists(conn, "birdnet_scores"):
        logger.info("Waiting for sensos.birdnet_scores table to be created.")
        time.sleep(60)


def get_disk_free_gb_and_percent(path: str) -> Optional[Dict[str, float]]:
    try:
        total, used, free = shutil.disk_usage(path)
        free_gb = free / (1024**3)
        percent_free = 100 * free / total if total else 0
        return {
            "disk_available_gb": round(free_gb, 2),
            "percent_free": round(percent_free, 2),
            "total_gb": round(total / (1024**3), 2),
        }
    except Exception as e:
        logger.warning(f"Could not get disk usage for {path}: {e}")
        return None


def get_richest_week(conn):
    """
    Returns the start date of the week (as a datetime) with the most non-zeroed segments,
    using the calculated segment timestamp.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                date_trunc(
                    'week',
                    af.capture_timestamp + (ag.start_frame * INTERVAL '1 second') / af.sample_rate
                ) AS week_start,
                COUNT(*) AS num_segments
            FROM sensos.audio_segments ag
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE NOT ag.zeroed
            GROUP BY week_start
            ORDER BY num_segments DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row and row["week_start"]:
            logger.info(
                f"Richest week starts {row['week_start']} with {row['num_segments']} unzeroed segments."
            )
            return row["week_start"]
        else:
            logger.info("No non-zeroed segments found in any week.")
            return None


def get_lowest_score_segment_for_frequent_label(conn, week_start):
    """
    Finds the segment in the given week whose top BirdNET label is the most frequent label,
    and whose top label's score is the lowest among such segments.
    Returns the segment info and the top label/score.
    """
    from datetime import timedelta

    week_end = week_start + timedelta(weeks=1)

    with conn.cursor() as cur:
        cur.execute(
            """
            WITH week_segments AS (
                SELECT id
                FROM sensos.audio_segments
                WHERE NOT zeroed
                AND segment_start_time >= %s
                AND segment_start_time < %s
            ),
            top_scores AS (
                SELECT bs.segment_id, bs.label, bs.score
                FROM sensos.birdnet_scores bs
                INNER JOIN (
                    SELECT segment_id, MAX(score) AS max_score
                    FROM sensos.birdnet_scores
                    GROUP BY segment_id
                ) ms ON bs.segment_id = ms.segment_id AND bs.score = ms.max_score
                WHERE bs.segment_id IN (SELECT id FROM week_segments)
            ),
            most_freq_label AS (
                SELECT label
                FROM top_scores
                GROUP BY label
                ORDER BY COUNT(*) DESC
                LIMIT 1
            )
            SELECT ts.segment_id, ts.label, ts.score,
                af.file_path, af.id AS file_id,
                ag.channel, ag.start_frame, ag.end_frame
            FROM top_scores ts
            JOIN sensos.audio_segments ag ON ts.segment_id = ag.id
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE ts.label = (SELECT label FROM most_freq_label)
            AND af.file_path LIKE '%%.wav'
            ORDER BY ts.score ASC
            LIMIT 1
            """,
            (week_start, week_end),
        )
        row = cur.fetchone()
        if row:
            logger.info(
                f"Lowest score segment with most frequent label '{row['label']}' in week {week_start} "
                f"has score {row['score']} (segment id {row['segment_id']})."
            )
            return row
        else:
            logger.info(
                f"No segments found for most frequent label in week starting {week_start}"
            )
            return None


def zero_redundant_segments(conn, min_free_gb=32):
    while True:
        disk = get_disk_free_gb_and_percent(AUDIO_BASE)
        if disk is not None and disk["disk_available_gb"] > min_free_gb:
            logger.info("Enough disk space. Done.")
            break
        elif disk is None:
            logger.warning("Could not determine disk space. Skipping cleanup for now.")
            break

        week = get_richest_week(conn)
        if not week:
            logger.info("No more weeks to clean.")
            break

        seg = get_lowest_score_segment_for_frequent_label(conn, week)
        if not seg:
            logger.info(f"No segment to zero out in week {week}.")
            break

        logger.info(
            f"Zeroing segment {seg['segment_id']} (label='{seg['label']}', score={seg['score']}) "
            f"in week {week}."
        )
        zero_segment(conn, seg)

        logger.info("Pass complete. Checking disk again.")


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

                disk = get_disk_free_gb_and_percent(AUDIO_BASE)
                if disk and disk["disk_available_gb"] < 32:
                    logger.warning(
                        f"Disk space low ({disk['disk_available_gb']} GB left). Starting emergency cleanup."
                    )
                    zero_redundant_segments(conn, min_free_gb=64)

        except Exception as e:
            logger.error(f"Error: {e!r} ({type(e)})")
            logger.error(traceback.format_exc())
            time.sleep(60)


def main() -> None:
    time.sleep(60)
    main_loop()


if __name__ == "__main__":
    main()
