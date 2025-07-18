import os
import time
import psycopg
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Set, List
import soundfile as sf
import traceback

from db_utils import (
    connect_with_retry,
    wait_for_birdnet_table,
    has_new_segments,
    mark_new_segments_processed,
    zero_segments_below_threshold,
)

# === CONFIG & LOGGING ===
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


# === SEGMENT ZEROING, MERGING, & DELETION ===


def zero_human_segments(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sensos.audio_segments
            SET zeroed = TRUE
            WHERE id IN (
                SELECT s2.id
                FROM sensos.audio_segments s2
                JOIN (
                    SELECT s.file_id, s.channel, s.start_frame, s.end_frame
                    FROM sensos.audio_segments s
                    JOIN sensos.birdnet_scores b ON s.id = b.segment_id
                    WHERE b.label ILIKE '%human%' AND b.score >= %s
                ) as human
                ON s2.file_id = human.file_id AND s2.channel = human.channel
                    AND NOT (s2.end_frame <= human.start_frame OR s2.start_frame >= human.end_frame)
            )
            """,
            (BIRDNET_SCORE_THRESHOLD,),
        )
        conn.commit()
        logger.info("Zeroed out all segments overlapping human vocalizations.")


def merge_segments_with_same_label(conn):
    with conn.cursor() as cur:
        # For each file/channel, get all non-zeroed segments and their top label/score
        cur.execute(
            """
            SELECT s.id, s.file_id, s.channel, s.start_frame, s.end_frame,
                (SELECT label FROM sensos.birdnet_scores WHERE segment_id = s.id ORDER BY score DESC LIMIT 1) as top_label,
                (SELECT score FROM sensos.birdnet_scores WHERE segment_id = s.id ORDER BY score DESC LIMIT 1) as top_score
            FROM sensos.audio_segments s
            WHERE s.zeroed IS NOT TRUE
            ORDER BY s.file_id, s.channel, s.start_frame
        """
        )
        segs = cur.fetchall()

        from collections import defaultdict

        groups = defaultdict(list)
        for seg in segs:
            groups[(seg["file_id"], seg["channel"], seg["top_label"])].append(seg)

        for (file_id, channel, label), group in groups.items():
            group.sort(key=lambda x: x["start_frame"])
            run = []
            for seg in group:
                # Overlap or adjacency?
                if not run or seg["start_frame"] <= run[-1]["end_frame"]:
                    run.append(seg)
                else:
                    # Merge the current run if >1 segment
                    if len(run) > 1:
                        # Anchor is the segment with the highest top_score
                        anchor = max(run, key=lambda s: s["top_score"])
                        new_start = min(s["start_frame"] for s in run)
                        new_end = max(s["end_frame"] for s in run)
                        # Update anchor's bounds
                        cur.execute(
                            "UPDATE sensos.audio_segments SET start_frame = %s, end_frame = %s WHERE id = %s",
                            (new_start, new_end, anchor["id"]),
                        )
                        # Delete all other segments in the run
                        to_delete = tuple(
                            s["id"] for s in run if s["id"] != anchor["id"]
                        )
                        if to_delete:
                            cur.execute(
                                f"DELETE FROM sensos.audio_segments WHERE id IN %s",
                                (to_delete,),
                            )
                        conn.commit()
                        logger.info(
                            f"Merged {len(run)} segments (label={label}) into anchor {anchor['id']} (score={anchor['top_score']})"
                        )
                    run = [seg]
            # Handle last run
            if len(run) > 1:
                anchor = max(run, key=lambda s: s["top_score"])
                new_start = min(s["start_frame"] for s in run)
                new_end = max(s["end_frame"] for s in run)
                cur.execute(
                    "UPDATE sensos.audio_segments SET start_frame = %s, end_frame = %s WHERE id = %s",
                    (new_start, new_end, anchor["id"]),
                )
                to_delete = tuple(s["id"] for s in run if s["id"] != anchor["id"])
                if to_delete:
                    cur.execute(
                        f"DELETE FROM sensos.audio_segments WHERE id IN %s",
                        (to_delete,),
                    )
                conn.commit()
                logger.info(
                    f"Merged {len(run)} segments (label={label}) into anchor {anchor['id']} (score={anchor['top_score']})"
                )


def delete_fully_zeroed_files(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT af.id, af.file_path
            FROM sensos.audio_files af
            WHERE af.deleted = FALSE
            AND NOT EXISTS (
                SELECT 1 FROM sensos.audio_segments s WHERE s.file_id = af.id AND s.zeroed = FALSE
            )
        """
        )
        rows = cur.fetchall()
        for row in rows:
            path = AUDIO_BASE / row["file_path"]
            if path.exists():
                try:
                    path.unlink()
                    logger.info(f"Deleted file {path}")
                except Exception as e:
                    logger.error(f"Could not delete {path}: {e}")
            cur.execute(
                "UPDATE sensos.audio_files SET deleted = TRUE, deleted_at = NOW() WHERE id = %s",
                (row["id"],),
            )
        conn.commit()


# === MAIN LOOPS ===


def batch_postprocess(conn):
    zero_human_segments(conn)
    merge_segments_with_same_label(conn)
    zero_segments_below_threshold(conn, BIRDNET_SCORE_THRESHOLD)
    delete_fully_zeroed_files(conn)


def main_loop():
    while True:
        try:
            with connect_with_retry(DB_PARAMS) as conn:
                wait_for_birdnet_table(conn)
                if has_new_segments(conn):
                    batch_postprocess(conn)
                    mark_new_segments_processed(conn)
                else:
                    logger.info("No new segments. Sleeping...")
                    time.sleep(60)
        except Exception as e:
            logger.error(f"Error: {e!r}")
            logger.error(traceback.format_exc())
            time.sleep(60)


def main() -> None:
    time.sleep(60)
    main_loop()


if __name__ == "__main__":
    main()
