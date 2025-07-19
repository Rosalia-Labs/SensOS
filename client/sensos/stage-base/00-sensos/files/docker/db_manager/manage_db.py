import os
import time
import atexit
import signal
import psycopg
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Set, List
from collections import defaultdict
import soundfile as sf
import traceback

from db_utils import (
    connect_with_retry,
    wait_for_birdnet_table,
    get_unprocessed_segment_ids,
    mark_segments_processed,
)

TESTING = False
MAX_CYCLES = 5

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


def get_disk_free_mb(path: Path) -> Optional[float]:
    """
    Get the free disk space for a given Path, in MB.
    Returns float (MB), or None on error.
    """
    import shutil

    try:
        total, used, free = shutil.disk_usage(str(path))
        free_mb = free / (1024**2)
        return round(free_mb, 2)
    except Exception as e:
        logger.warning(f"Could not get disk usage for {path}: {e}")
        return None


def zero_segments_by_file(
    segments: List[Dict[str, Any]], audio_base: Path
) -> List[int]:

    zeroed_ids = []
    by_file = defaultdict(list)
    for seg in segments:
        by_file[audio_base / seg["file_path"]].append(seg)

    for file_path, segs in by_file.items():
        if not file_path.exists():
            logger.warning(f"Audio file not found: {file_path}")
            continue
        if TESTING:
            logger.info(
                f"[TESTING] Would zero {len(segs)} segments in {file_path.name}"
            )
            zeroed_ids.extend([s["id"] for s in segs])
            continue
        try:
            data, sr = sf.read(file_path, dtype="int32", always_2d=True)
            for seg in segs:
                ch = seg["channel"]
                start = seg["start_frame"]
                end = seg["end_frame"]
                data[start:end, ch] = 0
            tmp_path = file_path.with_suffix(file_path.suffix + ".zeroing.tmp")
            sf.write(tmp_path, data, sr, format="FLAC")
            tmp_path.replace(file_path)
            logger.info(f"Zeroed {len(segs)} segment(s) in {file_path.name}")
            zeroed_ids.extend([s["id"] for s in segs])
        except Exception as e:
            logger.error(f"Failed to zero segments in {file_path}: {e}")
    return zeroed_ids


def zero_human_segments(conn, segment_ids):
    if not segment_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.file_id, s.channel, s.start_frame, s.end_frame, f.file_path
            FROM sensos.audio_segments s
            JOIN sensos.audio_files f ON s.file_id = f.id
            JOIN sensos.birdnet_scores b ON s.id = b.segment_id
            WHERE s.processed = FALSE
              AND s.zeroed IS NOT TRUE
              AND s.id = ANY(%s)
              AND b.label ILIKE '%%human%%'
            """,
            (segment_ids,),
        )
        segments = cur.fetchall()
    if not segments:
        return

    zeroed_ids = zero_segments_by_file([dict(s) for s in segments], AUDIO_BASE)

    if zeroed_ids:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(zeroed_ids))
            sql = f"UPDATE sensos.audio_segments SET zeroed = TRUE WHERE id IN ({placeholders})"
            cur.execute(sql, zeroed_ids)
            conn.commit()


def merge_segments_with_same_label(conn, segment_ids):
    if not segment_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.file_id, s.channel, s.start_frame, s.end_frame,
                (SELECT label FROM sensos.birdnet_scores WHERE segment_id = s.id ORDER BY score DESC LIMIT 1) as top_label,
                (SELECT score FROM sensos.birdnet_scores WHERE segment_id = s.id ORDER BY score DESC LIMIT 1) as top_score
            FROM sensos.audio_segments s
            WHERE s.zeroed IS NOT TRUE AND s.processed = FALSE AND s.id = ANY(%s)
            ORDER BY s.file_id, s.channel, s.start_frame
            """,
            (segment_ids,),
        )
        segs = cur.fetchall()

        groups = defaultdict(list)
        for seg in segs:
            groups[(seg["file_id"], seg["channel"], seg["top_label"])].append(seg)
        for (file_id, channel, label), group in groups.items():
            group.sort(key=lambda x: x["start_frame"])
            run = []
            for seg in group:
                if not run or seg["start_frame"] <= run[-1]["end_frame"]:
                    run.append(seg)
                else:
                    if len(run) > 1:
                        anchor = max(run, key=lambda s: s["top_score"])
                        new_start = min(s["start_frame"] for s in run)
                        new_end = max(s["end_frame"] for s in run)
                        cur.execute(
                            "UPDATE sensos.audio_segments SET start_frame = %s, end_frame = %s WHERE id = %s",
                            (new_start, new_end, anchor["id"]),
                        )
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
                    placeholders = ",".join(["%s"] * len(to_delete))
                    sql = f"DELETE FROM sensos.audio_segments WHERE id IN ({placeholders})"
                    cur.execute(sql, to_delete)
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
                if TESTING:
                    logger.info(f"[TESTING] Would delete file {path}")
                else:
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


def pick_segments_for_thinning(conn, max_segments=1000):
    """
    Iteratively selects segments to thin, using dynamic week/label/score strategy.
    Returns a list of segments (dicts) for zeroing, in order.
    Does not touch disk or DB.
    """

    def not_in_clause(ids, field="s.id"):
        if not ids:
            return "", []
        placeholders = ",".join(["%s"] * len(ids))
        return f"AND {field} NOT IN ({placeholders})", list(ids)

    picked_ids = set()
    picked_segments = []

    while len(picked_segments) < max_segments:
        clause, params = not_in_clause(picked_ids)
        with conn.cursor() as cur:
            sql = f"""
                SELECT EXTRACT(WEEK FROM f.created_at)::int AS week_num,
                       SUM(s.end_frame - s.start_frame) AS total_frames
                FROM sensos.audio_segments s
                JOIN sensos.audio_files f ON s.file_id = f.id
                WHERE s.zeroed IS NOT TRUE {clause}
                GROUP BY week_num
                ORDER BY total_frames DESC
                LIMIT 1
            """
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row or not row["week_num"]:
                break
            week_num = int(row["week_num"])

        clause, params = not_in_clause(picked_ids)
        with conn.cursor() as cur:
            sql = f"""
                SELECT b.label, COUNT(*) AS cnt
                FROM sensos.audio_segments s
                JOIN sensos.audio_files f ON s.file_id = f.id
                JOIN sensos.birdnet_scores b ON s.id = b.segment_id
                WHERE s.zeroed IS NOT TRUE
                  AND EXTRACT(WEEK FROM f.created_at)::int = %s
                  {clause}
                  AND b.label = (
                        SELECT label FROM sensos.birdnet_scores WHERE segment_id = s.id ORDER BY score DESC LIMIT 1
                  )
                GROUP BY b.label
                ORDER BY cnt DESC
                LIMIT 1
            """
            cur.execute(sql, [week_num] + params)
            row = cur.fetchone()
            if not row:
                break
            label = row["label"]

        clause, params = not_in_clause(picked_ids)
        with conn.cursor() as cur:
            sql = f"""
                SELECT s.id, s.file_id, s.channel, s.start_frame, s.end_frame,
                       f.file_path,
                       (SELECT label FROM sensos.birdnet_scores WHERE segment_id = s.id ORDER BY score DESC LIMIT 1) as top_label,
                       (SELECT score FROM sensos.birdnet_scores WHERE segment_id = s.id ORDER BY score DESC LIMIT 1) as top_score
                FROM sensos.audio_segments s
                JOIN sensos.audio_files f ON s.file_id = f.id
                WHERE s.zeroed IS NOT TRUE
                  AND EXTRACT(WEEK FROM f.created_at)::int = %s
                  {clause}
                  AND (SELECT label FROM sensos.birdnet_scores WHERE segment_id = s.id ORDER BY score DESC LIMIT 1) = %s
                ORDER BY top_score ASC
                LIMIT 1
            """
            cur.execute(sql, [week_num] + params + [label])
            seg = cur.fetchone()
            if not seg:
                break
            seg = dict(seg)
            picked_segments.append(seg)
            picked_ids.add(seg["id"])
    return picked_segments


def thin_data_until_disk_usage_ok(
    conn, start_threshold=500, stop_threshold=1000, batch_size=1000
):
    """
    Picks all segments to thin (in optimal order), then zeroes them by file, then updates DB.
    """
    while True:
        free_mb = get_disk_free_mb(AUDIO_BASE)
        if free_mb is None:
            logger.warning("Could not determine free disk space. Aborting thinning.")
            return

        if free_mb > start_threshold:
            logger.info("Disk usage is within acceptable bounds, no thinning required.")
            return

        logger.info("Selecting segments for thinning...")
        segments = pick_segments_for_thinning(conn, batch_size)
        if not segments:
            logger.info("No eligible segments to thin (disk pressure remains).")
            return

        logger.info(f"Identified {len(segments)} segments for thinning.")

        zeroed_ids = zero_segments_by_file(segments, AUDIO_BASE)

        if zeroed_ids:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(zeroed_ids))
                sql = f"UPDATE sensos.audio_segments SET zeroed = TRUE WHERE id IN ({placeholders})"
                cur.execute(sql, zeroed_ids)
                conn.commit()

        free_mb = get_disk_free_mb(AUDIO_BASE)
        if free_mb is None:
            logger.warning("Could not determine free disk space. Aborting thinning.")
            return

        if free_mb > stop_threshold:
            logger.info("Disk usage is within acceptable bounds, no thinning required.")
            return

        logger.info(f"Disk free after thinning: {free_mb} MB")
        if free_mb > stop_threshold:
            logger.info("Disk free space now sufficient, stopping thinning.")
            return
        else:
            logger.info("Still under disk threshold, will thin more in next batch.")


def batch_postprocess(conn, segment_ids):
    zero_human_segments(conn, segment_ids)
    merge_segments_with_same_label(conn, segment_ids)
    thin_data_until_disk_usage_ok(conn)
    delete_fully_zeroed_files(conn)


def main_loop(conn):
    cycle = 0
    while True:
        if TESTING and cycle >= MAX_CYCLES:
            logger.info(f"[TESTING] Reached max cycles ({MAX_CYCLES}), exiting loop.")
            break
        cycle += 1
        try:
            wait_for_birdnet_table(conn)
            segment_ids = get_unprocessed_segment_ids(conn)
            if segment_ids:
                batch_postprocess(conn, segment_ids)
                mark_segments_processed(conn, segment_ids)
            else:
                logger.info("No new segments. Sleeping...")
                time.sleep(5 if TESTING else 60)
        except Exception as e:
            logger.error(f"Error: {e!r}")
            logger.error(traceback.format_exc())
            time.sleep(5 if TESTING else 60)


def run_with_testing_transaction():
    with connect_with_retry(DB_PARAMS) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("BEGIN;")
        logger.info("[TESTING] BEGIN issued, running in test transaction.")

        def rollback():
            logger.info("[TESTING] Rolling back all changes (END of dry run)")
            try:
                conn.rollback()
            except Exception as e:
                logger.error(f"[TESTING] Error during rollback: {e}")

        atexit.register(rollback)

        def handle_exit(signum, frame):
            logger.info(
                f"[TESTING] Caught signal {signum}, rolling back transaction and exiting."
            )
            rollback()
            raise SystemExit(1)

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, handle_exit)

        try:
            main_loop(conn)
        finally:
            rollback()


def main():
    global TESTING, MAX_CYCLES
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--testing", action="store_true")
    parser.add_argument("--cycles", type=int, default=5)
    args = parser.parse_args()
    TESTING = args.testing
    MAX_CYCLES = args.cycles

    if TESTING:
        logger.info("[TESTING] Starting in testing/dry-run mode!")
        run_with_testing_transaction()
    else:
        with connect_with_retry(DB_PARAMS) as conn:
            main_loop(conn)


if __name__ == "__main__":
    main()
