import os
import psycopg
from manage_db import batch_postprocess
from db_utils import mark_segments_processed

import numpy as np
import soundfile as sf
from pathlib import Path

DB_PARAMS = {
    "dbname": "testdb",
    "user": "testuser",
    "password": "testpass",
    "host": "test-pg",
    "port": 5432,
}

AUDIO_BASE = Path("/audio_recordings")


def make_test_audio(filename, nframes=3000, nchannels=1, sr=48000):
    path = AUDIO_BASE / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.random.randint(-32768, 32767, size=(nframes, nchannels), dtype=np.int16)
    sf.write(str(path), data, sr)


def setup_schema(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            DROP SCHEMA IF EXISTS sensos CASCADE;
            CREATE SCHEMA IF NOT EXISTS sensos;

            CREATE TABLE IF NOT EXISTS sensos.audio_files (
                id SERIAL PRIMARY KEY,
                file_path TEXT NOT NULL,
                deleted BOOLEAN DEFAULT FALSE,
                deleted_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sensos.audio_segments (
                id SERIAL PRIMARY KEY,
                file_id INTEGER REFERENCES sensos.audio_files(id),
                channel INTEGER,
                start_frame INTEGER,
                end_frame INTEGER,
                processed BOOLEAN DEFAULT FALSE,
                zeroed BOOLEAN DEFAULT FALSE
            );
            CREATE TABLE IF NOT EXISTS sensos.birdnet_scores (
                id SERIAL PRIMARY KEY,
                segment_id INTEGER REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
                label TEXT,
                score FLOAT
            );
            """
        )
        conn.commit()


def seed_data(conn):
    make_test_audio("fake1.wav", nframes=22050, nchannels=1, sr=22050)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sensos.audio_files (file_path) VALUES ('fake1.wav') RETURNING id;"
        )
        file_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO sensos.audio_segments (file_id, channel, start_frame, end_frame, processed, zeroed)
            VALUES (%s, 0, 0, 1000, FALSE, FALSE)
            RETURNING id;
            """,
            (file_id,),
        )
        seg1 = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO sensos.audio_segments (file_id, channel, start_frame, end_frame, processed, zeroed)
            VALUES (%s, 0, 1100, 2000, FALSE, FALSE)
            RETURNING id;
            """,
            (file_id,),
        )
        seg2 = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO sensos.birdnet_scores (segment_id, label, score) VALUES
            (%s, 'human_speech', 0.95),
            (%s, 'cardinal', 0.2)
            """,
            (seg1, seg2),
        )
        conn.commit()
    return [seg1, seg2]


def test_batch_postprocess():
    with psycopg.connect(**DB_PARAMS) as conn:
        conn.row_factory = psycopg.rows.dict_row

        setup_schema(conn)
        seg_ids = seed_data(conn)

        batch_postprocess(conn, seg_ids)
        mark_segments_processed(conn, seg_ids)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT zeroed FROM sensos.audio_segments WHERE id = %s", (seg_ids[0],)
            )
            zeroed_1 = cur.fetchone()["zeroed"]
            print("Segment 1 (human) zeroed:", zeroed_1)
            assert zeroed_1, "Segment 1 should be zeroed!"

            cur.execute(
                "SELECT zeroed FROM sensos.audio_segments WHERE id = %s", (seg_ids[1],)
            )
            zeroed_2 = cur.fetchone()["zeroed"]
            print("Segment 2 (bird) zeroed:", zeroed_2)
            assert not zeroed_2, "Segment 2 should not be zeroed!"

            cur.execute(
                "SELECT processed FROM sensos.audio_segments WHERE id = %s",
                (seg_ids[0],),
            )
            assert cur.fetchone()["processed"], "Segment 1 should be marked processed!"
            cur.execute(
                "SELECT processed FROM sensos.audio_segments WHERE id = %s",
                (seg_ids[1],),
            )
            assert cur.fetchone()["processed"], "Segment 2 should be marked processed!"


def seed_mergeable_data(conn):
    make_test_audio("fake2.wav", nframes=3000, nchannels=1, sr=48000)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sensos.audio_files (file_path) VALUES ('fake2.wav') RETURNING id;"
        )
        file_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO sensos.audio_segments (file_id, channel, start_frame, end_frame, processed, zeroed)
            VALUES (%s, 0, 0, 1000, FALSE, FALSE)
            RETURNING id;
            """,
            (file_id,),
        )
        seg1 = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO sensos.audio_segments (file_id, channel, start_frame, end_frame, processed, zeroed)
            VALUES (%s, 0, 1000, 2000, FALSE, FALSE)
            RETURNING id;
            """,
            (file_id,),
        )
        seg2 = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO sensos.birdnet_scores (segment_id, label, score) VALUES
            (%s, 'cardinal', 0.8),
            (%s, 'cardinal', 0.75)
            """,
            (seg1, seg2),
        )
        conn.commit()
    return [seg1, seg2]


def test_batch_postprocess_and_merging():
    with psycopg.connect(**DB_PARAMS) as conn:
        conn.row_factory = psycopg.rows.dict_row

        setup_schema(conn)

        seg_ids = seed_data(conn)
        batch_postprocess(conn, seg_ids)
        mark_segments_processed(conn, seg_ids)

        merge_ids = seed_mergeable_data(conn)
        batch_postprocess(conn, merge_ids)
        mark_segments_processed(conn, merge_ids)

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM sensos.audio_segments
                WHERE file_id = (SELECT file_id FROM sensos.audio_segments WHERE id = %s)
                  AND channel = 0
                  AND processed = TRUE
                  AND zeroed = FALSE
                """,
                (merge_ids[0],),
            )
            segments = cur.fetchall()
            print("Post-merge segments:", segments)
            assert len(segments) == 1, "Segments should be merged into one!"
            merged = segments[0]
            assert (
                merged["start_frame"] == 0 and merged["end_frame"] == 2000
            ), "Merged segment should cover full range!"
            print("Merged segment from 0 to 2000:", merged)


if __name__ == "__main__":
    test_batch_postprocess()
    test_batch_postprocess_and_merging()
    print("All tests passed.")
