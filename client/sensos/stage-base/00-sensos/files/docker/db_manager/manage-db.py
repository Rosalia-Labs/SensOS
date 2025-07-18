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
    mark_segment_zeroed,
    is_file_fully_zeroed,
    mark_file_deleted,
    has_new_segments,
    mark_new_segments_processed,
    zero_segments_below_threshold,
)

from storage_utils import (
    overwrite_segment_with_zeros,
    delete_audio_file_from_disk,
    get_disk_free_gb_and_percent,
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

# === SEGMENT QUERIES & UTILITIES ===


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


# === SEGMENT ZEROING, MERGING, & DELETION ===


def zero_segment(conn, seg: Dict[str, Any]) -> Optional[int]:
    if overwrite_segment_with_zeros(seg, AUDIO_BASE):
        mark_segment_zeroed(conn, seg["segment_id"])
        handle_fully_zeroed_file(conn, seg)
        return seg["file_id"]
    else:
        logger.warning(f"Segment {seg['segment_id']} not zeroed.")
        return None


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


# === FILE & SEGMENT COMPRESSION ===


def handle_fully_zeroed_file(conn: psycopg.Connection, seg: Dict[str, Any]) -> None:
    """Delete file from disk and mark as deleted in DB if all segments zeroed."""
    if is_file_fully_zeroed(conn, seg["file_id"]):
        delete_audio_file_from_disk(seg, AUDIO_BASE)
        mark_file_deleted(conn, seg["file_id"])


# === EMERGENCY CLEANUP / REDUNDANT ZEROING ===


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


# === MAIN LOOPS ===


def batch_postprocess(conn):
    logger.info("Step 1: Zero out human vocal (and overlapping) segments.")
    zero_human_segments(conn)

    logger.info(
        "Step 2: Merge overlapping/adjacent segments with same top label (anchor=highest score)."
    )
    merge_segments_with_same_label(conn)

    logger.info("Step 3: Zero out segments below BirdNET score threshold.")
    zero_segments_below_threshold(conn, BIRDNET_SCORE_THRESHOLD)

    logger.info("Step 4: Delete fully zeroed files from filesystem and DB.")
    delete_fully_zeroed_files(conn)


def main_loop():
    while True:
        try:
            with connect_with_retry(DB_PARAMS) as conn:
                wait_for_birdnet_table(conn)
                # Optionally, only fetch truly new/unprocessed segments
                if has_new_segments(conn):  # You'll need a utility for this!
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
