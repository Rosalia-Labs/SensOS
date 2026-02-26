# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

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
SEGMENT_BATCH_LIMIT = int(os.environ.get("SEGMENT_BATCH_LIMIT", "5000"))
PG_WORK_MEM_MB = int(os.environ.get("PG_WORK_MEM_MB", "64"))
EMERGENCY_DELETE_MAX_FILES = int(os.environ.get("EMERGENCY_DELETE_MAX_FILES", "25"))
# Only delete whole audio files when we're truly out of space.
EMERGENCY_TRIGGER_MB = int(os.environ.get("EMERGENCY_TRIGGER_MB", "200"))
DISK_START_THRESHOLD_MB = int(os.environ.get("DISK_START_THRESHOLD_MB", "5000"))
DISK_STOP_THRESHOLD_MB = int(os.environ.get("DISK_STOP_THRESHOLD_MB", "10000"))
THIN_BATCH_SIZE = int(os.environ.get("THIN_BATCH_SIZE", "1000"))
PG_STATEMENT_TIMEOUT_MS = int(os.environ.get("PG_STATEMENT_TIMEOUT_MS", "15000"))
PG_LOCK_TIMEOUT_MS = int(os.environ.get("PG_LOCK_TIMEOUT_MS", "2000"))
THIN_LOG_IDS = os.environ.get("THIN_LOG_IDS", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

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


def get_unprocessed_segment_ids_batch(conn, limit: int) -> List[int]:
    """
    Fetch a bounded batch of unprocessed segment IDs.

    Compatibility note: some deployed images may still have an older
    `db_utils.get_unprocessed_segment_ids` that doesn't accept `limit`.
    """
    try:
        return get_unprocessed_segment_ids(conn, limit=limit)
    except TypeError:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM sensos.audio_segments
                WHERE processed = FALSE
                ORDER BY id ASC
                LIMIT %s
                """,
                (limit,),
            )
            return [row["id"] for row in cur.fetchall()]


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
    segments: List[Dict[str, Any]], audio_base: Path, conn
) -> List[int]:

    zeroed_ids = []
    by_file = defaultdict(list)
    for seg in segments:
        by_file[audio_base / seg["file_path"]].append(seg)

    for file_path, segs in by_file.items():
        if not file_path.exists():
            logger.warning(f"Audio file not found: {file_path}")
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE sensos.audio_files SET deleted = TRUE, deleted_at = NOW() WHERE id = %s",
                        (segs[0]["file_id"],),
                    )
                    cur.execute(
                        "UPDATE sensos.audio_segments SET zeroed = TRUE WHERE id = ANY(%s)",
                        ([s["id"] for s in segs],),
                    )
                    conn.commit()
                    logger.info(f"Marked file as deleted in DB: {segs[0]['file_path']}")
            except Exception as e:
                logger.error(f"Failed to mark file deleted in DB: {file_path} — {e}")
                conn.rollback()
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
            new_path = file_path.with_suffix(".flac")
            sf.write(new_path, data, sr, format="FLAC")
            if file_path != new_path and file_path.exists():
                try:
                    file_path.unlink()
                except Exception as e:
                    logger.warning(f"Could not remove original file {file_path}: {e}")
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

    zeroed_ids = zero_segments_by_file([dict(s) for s in segments], AUDIO_BASE, conn)

    if zeroed_ids:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(zeroed_ids))
            sql = f"UPDATE sensos.audio_segments SET zeroed = TRUE WHERE id IN ({placeholders})"
            cur.execute(sql, zeroed_ids)
            conn.commit()


def merge_segments_with_same_label(conn, segment_ids):
    if not segment_ids:
        return

    def _merge_segment_run(cur, run, label):
        if len(run) <= 1:
            return
        anchor = max(run, key=lambda s: s["top_score"])
        new_start = min(s["start_frame"] for s in run)
        new_end = max(s["end_frame"] for s in run)
        to_delete = [s["id"] for s in run if s["id"] != anchor["id"]]
        if to_delete:
            placeholders = ",".join(["%s"] * len(to_delete))
            cur.execute(
                f"DELETE FROM sensos.audio_segments WHERE id IN ({placeholders})",
                to_delete,
            )
        cur.execute(
            "UPDATE sensos.audio_segments SET start_frame = %s, end_frame = %s WHERE id = %s",
            (new_start, new_end, anchor["id"]),
        )
        logger.info(
            f"Merged {len(run)} segments (label={label}) into anchor {anchor['id']} (score={anchor['top_score']})"
        )

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
                    _merge_segment_run(cur, run, label)
                    conn.commit()
                    run = [seg]
            _merge_segment_run(cur, run, label)
            conn.commit()


def delete_fully_zeroed_files(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT af.id, af.file_path
            FROM sensos.audio_files af
            WHERE af.deleted IS NOT TRUE
            AND NOT EXISTS (
                SELECT 1
                FROM sensos.audio_segments s
                WHERE s.file_id = af.id
                  AND s.zeroed IS NOT TRUE
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


def emergency_delete_random_audio_files(
    conn, audio_base: Path, target_free_mb: float, max_files: int = 25
) -> int:
    """
    Emergency escape hatch: if Postgres can't even run thinning queries (e.g. DiskFull
    creating pgsql_tmp files), delete whole audio files on disk to free space.

    Best-effort: attempts to mark corresponding `sensos.audio_files` rows as deleted
    by matching `file_path` to the relative path under `audio_base`.
    """
    import random

    seed_env = os.environ.get("EMERGENCY_DELETE_SEED")
    rng = random.Random(int(seed_env)) if seed_env else random.Random()

    free_mb = get_disk_free_mb(audio_base)
    if free_mb is None:
        logger.warning("Emergency delete: could not determine free disk space.")
        return 0
    if free_mb >= target_free_mb:
        return 0

    allowed_suffixes = {".wav", ".flac", ".ogg", ".aiff", ".aif", ".mp3", ".m4a"}
    candidates: List[Path] = []
    try:
        for p in audio_base.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in allowed_suffixes:
                continue
            candidates.append(p)
    except Exception as e:
        logger.error(f"Emergency delete: failed to scan {audio_base}: {e}")
        return 0

    logger.warning(
        f"Emergency delete: considering {len(candidates)} candidate audio file(s)"
        + (f" (seed={seed_env})" if seed_env else "")
        + f"; will delete up to {max_files}."
    )

    rng.shuffle(candidates)
    deleted: List[Path] = []
    for p in candidates:
        if len(deleted) >= max_files:
            break
        free_mb = get_disk_free_mb(audio_base)
        if free_mb is not None and free_mb >= target_free_mb:
            break
        try:
            if TESTING:
                logger.info(f"[TESTING] Would delete audio file {p}")
            else:
                p.unlink()
                logger.warning(f"Emergency randomly deleted audio file {p}")
            deleted.append(p)
        except Exception as e:
            logger.error(f"Emergency delete: could not delete {p}: {e}")

    if deleted and not TESTING:
        try:
            with conn.cursor() as cur:
                for p in deleted:
                    rel = p.relative_to(audio_base).as_posix()
                    cur.execute(
                        "UPDATE sensos.audio_files SET deleted = TRUE, deleted_at = NOW() WHERE file_path = %s",
                        (rel,),
                    )
            conn.commit()
        except Exception as e:
            logger.error(
                f"Emergency delete: failed to mark deleted files in DB (will retry later): {e}"
            )
            try:
                conn.rollback()
            except Exception:
                pass

    return len(deleted)


def emergency_delete_oldest_audio_files(*args, **kwargs) -> int:
    # Backwards-compatible alias (behavior is now random).
    return emergency_delete_random_audio_files(*args, **kwargs)


def pick_segments_for_thinning_simple(conn, max_segments=1000, segment_ids=None):
    """
    Low-DB-cost thinning fallback used when the full week/label strategy can't run
    (e.g. Postgres DiskFull while creating temp files).

    Intentionally simple: picks the earliest un-zeroed segments (optionally scoped)
    without BirdNET label aggregation.
    """
    with conn.cursor() as cur:
        cur.execute(f"SET LOCAL work_mem = '{PG_WORK_MEM_MB}MB'")
        cur.execute(f"SET LOCAL statement_timeout = '{PG_STATEMENT_TIMEOUT_MS}ms'")
        cur.execute(f"SET LOCAL lock_timeout = '{PG_LOCK_TIMEOUT_MS}ms'")

    segment_clause = ""
    params: List[Any] = []
    if segment_ids:
        segment_clause = "AND s.id = ANY(%s)"
        params.append(segment_ids)

    with conn.cursor() as cur:
        sql = f"""
            SELECT s.id, s.file_id, s.channel, s.start_frame, s.end_frame, f.file_path
            FROM sensos.audio_segments s
            JOIN sensos.audio_files f ON s.file_id = f.id
            WHERE s.zeroed IS NOT TRUE
              AND f.deleted IS NOT TRUE
              {segment_clause}
            ORDER BY s.id ASC
            LIMIT %s
        """
        cur.execute(sql, params + [max_segments])
        return [dict(r) for r in cur.fetchall()]


def pick_segments_for_thinning(conn, max_segments=1000, segment_ids=None):
    """
    Iteratively selects segments to thin, using dynamic week/label/score strategy.
    Returns a list of segments (dicts) for zeroing, in order.
    Does not touch disk or DB.
    """
    with conn.cursor() as cur:
        # Prefer using memory over spilling to `pgsql_tmp` when disk is under pressure.
        cur.execute(f"SET LOCAL work_mem = '{PG_WORK_MEM_MB}MB'")
        cur.execute(f"SET LOCAL statement_timeout = '{PG_STATEMENT_TIMEOUT_MS}ms'")
        cur.execute(f"SET LOCAL lock_timeout = '{PG_LOCK_TIMEOUT_MS}ms'")

    def not_in_clause(ids, field="s.id"):
        if not ids:
            return "", []
        placeholders = ",".join(["%s"] * len(ids))
        return f"AND {field} NOT IN ({placeholders})", list(ids)

    segment_clause = ""
    segment_params = []
    if segment_ids:
        segment_clause = "AND s.id = ANY(%s)"
        segment_params = [segment_ids]

    picked_ids = set()
    picked_segments = []
    started_at = time.monotonic()

    while len(picked_segments) < max_segments:
        if len(picked_segments) and len(picked_segments) % 50 == 0:
            logger.info(
                f"Thinning selection progress: picked {len(picked_segments)}/{max_segments} segments "
                f"(elapsed {time.monotonic() - started_at:.1f}s)"
            )
        clause, params = not_in_clause(picked_ids)
        with conn.cursor() as cur:
            # audio_files has `capture_timestamp`/`cataloged_at` (not `created_at`).
            # audio_segments has `created_at` as a safe fallback.
            sql = f"""
                SELECT EXTRACT(WEEK FROM COALESCE(f.capture_timestamp, f.cataloged_at, s.created_at))::int AS week_num,
                       SUM(s.end_frame - s.start_frame) AS total_frames
                FROM sensos.audio_segments s
                JOIN sensos.audio_files f ON s.file_id = f.id
                WHERE s.zeroed IS NOT TRUE
                  AND f.deleted IS NOT TRUE
                  {segment_clause}
                  {clause}
                GROUP BY week_num
                ORDER BY total_frames DESC
                LIMIT 1
            """
            cur.execute(sql, segment_params + params)
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
                  AND f.deleted IS NOT TRUE
                  AND EXTRACT(WEEK FROM COALESCE(f.capture_timestamp, f.cataloged_at, s.created_at))::int = %s
                  {segment_clause}
                  {clause}
                  AND b.label = (
                        SELECT label FROM sensos.birdnet_scores WHERE segment_id = s.id ORDER BY score DESC LIMIT 1
                  )
                GROUP BY b.label
                ORDER BY cnt DESC
                LIMIT 1
            """
            cur.execute(sql, [week_num] + segment_params + params)
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
                  AND f.deleted IS NOT TRUE
                  AND EXTRACT(WEEK FROM COALESCE(f.capture_timestamp, f.cataloged_at, s.created_at))::int = %s
                  {segment_clause}
                  {clause}
                  AND (SELECT label FROM sensos.birdnet_scores WHERE segment_id = s.id ORDER BY score DESC LIMIT 1) = %s
                ORDER BY top_score ASC
                LIMIT 1
            """
            cur.execute(sql, [week_num] + segment_params + params + [label])
            seg = cur.fetchone()
            if not seg:
                break
            seg = dict(seg)
            picked_segments.append(seg)
            picked_ids.add(seg["id"])
    return picked_segments


def thin_data_until_disk_usage_ok(
    conn,
    start_threshold=500,
    stop_threshold=1000,
    batch_size=1000,
    segment_ids=None,
):
    """
    Picks segments to thin (in optimal order), then zeroes them by file, then updates DB.

    When `segment_ids` is provided, thinning is constrained to that set to avoid
    expensive full-table queries that can fail when Postgres is out of temp space.
    """
    # Hysteresis: only start thinning below `start_threshold`, but once we start,
    # continue until we reach `stop_threshold`.
    started = False
    active_segment_ids = segment_ids
    last_selected_ids = None
    stalled_batches = 0

    def _repeatable_read_readonly_tx():
        isolation_level = getattr(getattr(psycopg, "IsolationLevel", None), "REPEATABLE_READ", None)
        isolation_level = isolation_level or "repeatable read"
        return conn.transaction(isolation_level=isolation_level, read_only=True)

    def _select_segments_with_stable_snapshot(*, use_simple: bool) -> List[Dict[str, Any]]:
        # Use a stable snapshot so segments whose BirdNET scores arrive mid-selection
        # don't influence/enter the current thinning batch.
        with _repeatable_read_readonly_tx():
            if use_simple:
                return pick_segments_for_thinning_simple(
                    conn, batch_size, segment_ids=active_segment_ids
                )
            return pick_segments_for_thinning(conn, batch_size, segment_ids=active_segment_ids)

    while True:
        free_mb = get_disk_free_mb(AUDIO_BASE)
        if free_mb is None:
            logger.warning("Could not determine free disk space. Aborting thinning.")
            return
        free_before_mb = free_mb

        if free_mb > stop_threshold:
            logger.info("Disk usage is within acceptable bounds, no thinning required.")
            return
        if not started and free_mb > start_threshold:
            logger.info("Disk usage is within acceptable bounds, no thinning required.")
            return

        logger.warning(
            f"Disk pressure: free={free_mb} MB (start={start_threshold} MB, stop={stop_threshold} MB)"
            + (" [scoped]" if active_segment_ids else " [global]")
        )
        logger.info("Selecting segments for thinning...")
        selection_started = time.monotonic()
        try:
            segments = _select_segments_with_stable_snapshot(use_simple=False)
        except Exception as e:
            disk_full_exc = getattr(getattr(psycopg, "errors", None), "DiskFull", None)
            query_canceled_exc = getattr(
                getattr(psycopg, "errors", None), "QueryCanceled", None
            )
            if not (
                (disk_full_exc and isinstance(e, disk_full_exc))
                or (query_canceled_exc and isinstance(e, query_canceled_exc))
            ):
                raise
            logger.error(f"Thinning selection failed (will fall back): {e!r}")
            logger.warning(
                "Falling back to simple thinning selection (no label aggregation)."
            )
            try:
                segments = _select_segments_with_stable_snapshot(use_simple=True)
            except Exception as fallback_e:
                if (disk_full_exc and isinstance(fallback_e, disk_full_exc)) or (
                    query_canceled_exc and isinstance(fallback_e, query_canceled_exc)
                ):
                    segments = []
                else:
                    raise
            if not segments:
                if free_mb <= EMERGENCY_TRIGGER_MB:
                    logger.warning(
                        "Emergency mode: disk is critically low; deleting random audio files."
                    )
                    deleted = emergency_delete_random_audio_files(
                        conn,
                        AUDIO_BASE,
                        target_free_mb=stop_threshold,
                        max_files=EMERGENCY_DELETE_MAX_FILES,
                    )
                    logger.warning(f"Emergency deletion removed {deleted} file(s).")
                    started = True
                    if deleted <= 0:
                        return
                    continue
                logger.warning(
                    f"Emergency deletion skipped: free={free_mb} MB is above EMERGENCY_TRIGGER_MB={EMERGENCY_TRIGGER_MB} MB."
                )
                return
        finally:
            logger.info(
                f"Thinning selection finished in {time.monotonic() - selection_started:.1f}s"
            )

        if not segments:
            logger.warning(
                "Thinning selection returned 0 segments; cannot make progress with current selection."
            )
            if active_segment_ids is not None:
                logger.warning(
                    "Scoped thinning appears exhausted/stalled; switching to global thinning selection."
                )
                active_segment_ids = None
                continue
            try:
                delete_fully_zeroed_files(conn)
            except Exception as e:
                logger.warning(f"Could not delete fully-zeroed files: {e!r}")

            free_mb_after = get_disk_free_mb(AUDIO_BASE)
            if free_mb_after is not None and free_mb_after > stop_threshold:
                logger.info("Disk free space now sufficient, stopping thinning.")
                return
            stalled_batches += 1
            if free_mb_after is not None and free_mb_after <= start_threshold and stalled_batches >= 3:
                logger.warning(
                    "Thinning is stalled and disk is still under threshold; deleting whole audio files to recover space."
                )
                deleted = emergency_delete_random_audio_files(
                    conn,
                    AUDIO_BASE,
                    target_free_mb=stop_threshold,
                    max_files=EMERGENCY_DELETE_MAX_FILES,
                )
                logger.warning(f"Emergency deletion removed {deleted} file(s).")
                if deleted <= 0:
                    return
                stalled_batches = 0
                continue
            if not TESTING:
                time.sleep(1)
            continue

        if THIN_LOG_IDS:
            selected_ids = [s.get("id") for s in segments if "id" in s]
            selected_ids = [i for i in selected_ids if i is not None]
            preview = selected_ids[:20]
            if last_selected_ids is not None and tuple(selected_ids) == last_selected_ids:
                logger.warning(
                    f"Thinning selection returned the same IDs as last batch ({len(selected_ids)} ids)."
                )
            last_selected_ids = tuple(selected_ids)
            files_preview = []
            try:
                files_preview = sorted({s.get("file_path") for s in segments if s.get("file_path")})[:10]
            except Exception:
                files_preview = []
            logger.info(
                f"Selected {len(selected_ids)} segment id(s) for thinning; first={preview}; files={files_preview}"
            )

        logger.info(f"Identified {len(segments)} segments for thinning.")

        zeroed_ids = zero_segments_by_file(segments, AUDIO_BASE, conn)

        if zeroed_ids:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(zeroed_ids))
                sql = f"UPDATE sensos.audio_segments SET zeroed = TRUE WHERE id IN ({placeholders})"
                cur.execute(sql, zeroed_ids)
                conn.commit()
            try:
                delete_fully_zeroed_files(conn)
            except Exception as e:
                logger.warning(f"Could not delete fully-zeroed files after thinning: {e!r}")
            if THIN_LOG_IDS:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT COUNT(*) AS n FROM sensos.audio_segments WHERE id = ANY(%s) AND zeroed = TRUE",
                            (zeroed_ids,),
                        )
                        row = cur.fetchone()
                    logger.info(
                        f"Marked {row['n'] if row else 'unknown'} segment(s) as zeroed in DB (expected {len(zeroed_ids)})."
                    )
                except Exception as e:
                    logger.warning(f"Could not verify DB zeroed flags: {e!r}")

        started = True
        free_mb = get_disk_free_mb(AUDIO_BASE)
        if free_mb is None:
            logger.warning("Could not determine free disk space. Aborting thinning.")
            return

        logger.info(f"Disk free after thinning: {free_mb} MB")
        if free_mb <= free_before_mb + 1.0:
            logger.warning(
                f"Thinning batch reclaimed little/no space (before={free_before_mb} MB, after={free_mb} MB)."
            )
            stalled_batches += 1
            if free_mb <= EMERGENCY_TRIGGER_MB:
                logger.warning(
                    "Disk is critically low and thinning isn't reclaiming space; deleting random audio files."
                )
                deleted = emergency_delete_random_audio_files(
                    conn,
                    AUDIO_BASE,
                    target_free_mb=stop_threshold,
                    max_files=EMERGENCY_DELETE_MAX_FILES,
                )
                logger.warning(f"Emergency deletion removed {deleted} file(s).")
                if deleted <= 0:
                    return
                continue
            if (
                active_segment_ids is None
                and stalled_batches >= 5
                and free_mb <= start_threshold
            ):
                logger.warning(
                    "Disk is still under threshold and thinning isn't reclaiming space; deleting whole audio files to recover space."
                )
                deleted = emergency_delete_random_audio_files(
                    conn,
                    AUDIO_BASE,
                    target_free_mb=stop_threshold,
                    max_files=EMERGENCY_DELETE_MAX_FILES,
                )
                logger.warning(f"Emergency deletion removed {deleted} file(s).")
                if deleted <= 0:
                    return
                stalled_batches = 0
                continue
            if (
                active_segment_ids is not None
                and stalled_batches >= 3
                and free_mb <= start_threshold
            ):
                logger.warning(
                    "Scoped thinning isn't reclaiming space; switching to global thinning selection."
                )
                active_segment_ids = None
                stalled_batches = 0
                continue
            if not TESTING:
                time.sleep(1)
        else:
            stalled_batches = 0
        if free_mb > stop_threshold:
            logger.info("Disk free space now sufficient, stopping thinning.")
            return
        logger.info("Still under disk threshold, will thin more in next batch.")


def batch_postprocess(conn, segment_ids):
    zero_human_segments(conn, segment_ids)
    merge_segments_with_same_label(conn, segment_ids)
    thin_data_until_disk_usage_ok(
        conn,
        start_threshold=DISK_START_THRESHOLD_MB,
        stop_threshold=DISK_STOP_THRESHOLD_MB,
        batch_size=THIN_BATCH_SIZE,
        segment_ids=segment_ids,
    )
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
            segment_ids = get_unprocessed_segment_ids_batch(conn, SEGMENT_BATCH_LIMIT)
            if segment_ids:
                batch_postprocess(conn, segment_ids)
                mark_segments_processed(conn, segment_ids)
            else:
                thin_data_until_disk_usage_ok(
                    conn,
                    start_threshold=DISK_START_THRESHOLD_MB,
                    stop_threshold=DISK_STOP_THRESHOLD_MB,
                    batch_size=THIN_BATCH_SIZE,
                    segment_ids=None,
                )
                logger.info("No new segments. Sleeping...")
                time.sleep(5 if TESTING else 60)
        except Exception as e:
            logger.error(f"Error: {e!r}")
            logger.error(traceback.format_exc())
            try:
                conn.rollback()
                logger.info("Rolled back failed transaction.")
            except Exception as rollback_error:
                logger.error(f"Error during rollback: {rollback_error}")
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
