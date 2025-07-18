import os
import psycopg
from db_utils import (
    connect_with_retry,
    get_unprocessed_segment_ids,
    mark_segments_processed,
    zero_segments_below_threshold,
)

DB_PARAMS = {
    "dbname": "testdb",
    "user": "testuser",
    "password": "testpass",
    "host": "test-pg",
    "port": 5432,
}


def setup_schema(conn):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS sensos.birdnet_scores CASCADE;")
        cur.execute("DROP TABLE IF EXISTS sensos.audio_segments CASCADE;")
        cur.execute("DROP SCHEMA IF EXISTS sensos CASCADE;")
        cur.execute("CREATE SCHEMA sensos;")
        cur.execute(
            """
        CREATE TABLE sensos.audio_segments (
            id SERIAL PRIMARY KEY,
            file_id INTEGER,
            channel INT,
            start_frame BIGINT,
            end_frame BIGINT,
            zeroed BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed BOOLEAN NOT NULL DEFAULT FALSE
        );
        """
        )
        cur.execute(
            """
        CREATE TABLE sensos.birdnet_scores (
            segment_id INTEGER REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
            label TEXT,
            score FLOAT
        );
        """
        )
        conn.commit()


def seed_data(conn):
    with conn.cursor() as cur:
        # Insert 5 segments
        for i in range(5):
            cur.execute(
                "INSERT INTO sensos.audio_segments (file_id, channel, start_frame, end_frame) VALUES (1, 0, %s, %s) RETURNING id",
                (i * 100, (i + 1) * 100),
            )
            seg_id = cur.fetchone()[0]
            # Assign BirdNET scores: 0.05, 0.2, 0.09, 0.15, 0.07
            score = [0.05, 0.2, 0.09, 0.15, 0.07][i]
            cur.execute(
                "INSERT INTO sensos.birdnet_scores (segment_id, label, score) VALUES (%s, 'testlabel', %s)",
                (seg_id, score),
            )
        conn.commit()


def print_segments(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, zeroed, processed FROM sensos.audio_segments ORDER BY id"
        )
        print("\nSegments:")
        for row in cur.fetchall():
            print(row)


def main():
    conn = connect_with_retry(DB_PARAMS)
    setup_schema(conn)
    seed_data(conn)
    print("Before batch:")
    print_segments(conn)
    segment_ids = get_unprocessed_segment_ids(conn)
    zero_segments_below_threshold(conn, 0.1, segment_ids)
    mark_segments_processed(conn, segment_ids)
    print("After batch:")
    print_segments(conn)
    conn.close()


if __name__ == "__main__":
    main()
