# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

# db_utils.py
import time
import psycopg
from typing import Optional, Dict, Any, List


def connect_with_retry(DB_PARAMS: dict) -> psycopg.Connection:
    """Try to connect to Postgres with retry."""
    while True:
        try:
            conn = psycopg.connect(**DB_PARAMS)
            conn.row_factory = psycopg.rows.dict_row
            return conn
        except Exception as e:
            print(f"Waiting for DB connection: {e}")
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


def wait_for_birdnet_table(conn: psycopg.Connection) -> None:
    """Wait for BirdNET table to exist before processing."""
    while not table_exists(conn, "birdnet_scores"):
        print("Waiting for sensos.birdnet_scores table to be created.")
        time.sleep(60)


def mark_segment_zeroed(conn: psycopg.Connection, segment_id: int) -> None:
    """Mark segment as zeroed (erased)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sensos.audio_segments SET zeroed = TRUE WHERE id = %s",
            (segment_id,),
        )
        conn.commit()


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
        return result["all_zeroed"]


def mark_file_deleted(conn: psycopg.Connection, file_id: int) -> None:
    """Mark file as deleted in DB."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sensos.audio_files SET deleted = TRUE, deleted_at = NOW() WHERE id = %s",
            (file_id,),
        )
        conn.commit()


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


def has_new_segments(conn) -> bool:
    """Returns True if there are unprocessed segments."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM sensos.audio_segments WHERE processed = FALSE)"
        )
        return cur.fetchone()["exists"]


def mark_new_segments_processed(conn):
    """Marks all unprocessed segments as processed."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sensos.audio_segments SET processed = TRUE WHERE processed = FALSE"
        )
        conn.commit()


def get_unprocessed_segment_ids(conn, limit: Optional[int] = None) -> List[int]:
    """Return segment IDs that are unprocessed (processed=FALSE).

    If limit is provided, returns up to `limit` IDs (ordered by id ascending).
    """
    with conn.cursor() as cur:
        if limit is None:
            cur.execute("SELECT id FROM sensos.audio_segments WHERE processed = FALSE")
        else:
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


def mark_segments_processed(conn, segment_ids):
    """Mark the provided segment IDs as processed."""
    if not segment_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sensos.audio_segments SET processed = TRUE WHERE id = ANY(%s)",
            (segment_ids,),
        )
        conn.commit()


def zero_segments_below_threshold(conn, threshold: float, segment_ids):
    """
    Zero out all *unprocessed* segments in the snapshot whose highest BirdNET score is below the given threshold.
    """
    if not segment_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sensos.audio_segments
            SET zeroed = TRUE
            WHERE zeroed = FALSE AND processed = FALSE
              AND id = ANY(%s)
              AND id IN (
                  SELECT s.id
                  FROM sensos.audio_segments s
                  JOIN (
                      SELECT segment_id, MAX(score) as max_score
                      FROM sensos.birdnet_scores
                      GROUP BY segment_id
                  ) maxes ON s.id = maxes.segment_id
                  WHERE maxes.max_score < %s AND s.id = ANY(%s)
              )
            """,
            (segment_ids, threshold, segment_ids),
        )
        conn.commit()
        print(f"Zeroed all segments below BirdNET score threshold ({threshold})")
