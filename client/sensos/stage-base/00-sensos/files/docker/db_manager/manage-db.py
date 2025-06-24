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

AUDIO_BASE = Path("/audio_recordings")

HUMAN_VOCAL_SCORE_THRESHOLD = float(os.environ.get("HUMAN_VOCAL_SCORE_THRESHOLD", 0.1))
BIRDNET_SCORE_THRESHOLD = float(os.environ.get("BIRDNET_SCORE_THRESHOLD", 0.1))


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
        return cur.fetchone()[0]


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


def zero_human_vocal_segments(conn) -> tuple[bool, bool]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ag.id AS segment_id,
                af.file_path,
                ag.channel,
                ag.start_frame,
                ag.end_frame
            FROM sensos.birdnet_scores bs
            JOIN sensos.audio_segments ag ON bs.segment_id = ag.id
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE bs.label ILIKE '%Human vocal%'
            AND bs.score >= %s
            AND af.file_path IS NOT NULL
            AND NOT ag.vocal_check
            ORDER BY ag.start_frame
            LIMIT 1
            """,
            (HUMAN_VOCAL_SCORE_THRESHOLD,),
        )

        seg = cur.fetchone()

    if not seg:
        logger.info("No more human vocal segments to process.")
        return False, False

    frame_count = seg["end_frame"] - seg["start_frame"]
    if frame_count <= 0:
        logger.warning(f"Invalid frame range for segment {seg['segment_id']}")
        return False

    success = overwrite_segment_with_zeros(
        seg["file_path"], seg["start_frame"], frame_count, seg["channel"]
    )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sensos.audio_segments SET vocal_check = TRUE WHERE id = %s",
            (seg["segment_id"],),
        )
        conn.commit()

    return True, success


def zero_birdnet_low_score_segments(conn) -> tuple[bool, bool]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ag.id AS segment_id,
                af.file_path,
                ag.channel,
                ag.start_frame,
                ag.end_frame,
                bs.score
            FROM sensos.birdnet_scores bs
            JOIN sensos.audio_segments ag ON bs.segment_id = ag.id
            JOIN sensos.audio_files af ON ag.file_id = af.id
            WHERE bs.score < %s
            AND af.file_path IS NOT NULL
            AND NOT ag.vocal_check
            ORDER BY ag.start_frame
            LIMIT 1
            """,
            (BIRDNET_SCORE_THRESHOLD,),
        )
        seg = cur.fetchone()

    if not seg:
        logger.info("No more low-score segments to process.")
        return False, False

    frame_count = seg["end_frame"] - seg["start_frame"]
    if frame_count <= 0:
        logger.warning(f"Invalid frame range for segment {seg['segment_id']}")
        return False, False

    success = overwrite_segment_with_zeros(
        seg["file_path"], seg["start_frame"], frame_count, seg["channel"]
    )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sensos.audio_segments SET vocal_check = TRUE WHERE id = %s",
            (seg["segment_id"],),
        )
        conn.commit()

    return True, success


def check_segment_for_deletion(conn, segment_id) -> bool:
    """
    Returns True if the segment should be deleted (zeroed), False otherwise.
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
        for row in scores:
            if (
                row["label"].lower().startswith("human vocal")
                and row["score"] >= HUMAN_VOCAL_SCORE_THRESHOLD
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


def get_next_unchecked_segments(conn, batch_size=100):
    with conn.cursor() as cur:
        cur.execute(
            f"""
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


def mark_file_deleted(cur, file_id):
    cur.execute(
        "UPDATE sensos.audio_files SET deleted = TRUE, deleted_at = NOW() WHERE id = %s",
        (file_id,),
    )


def is_file_fully_zeroed(conn, file_id):
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


def all_segments_checked(conn, file_id):
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


def compress_file_if_done(conn, seg):
    file_id = seg["file_id"]
    file_path = AUDIO_BASE / seg["file_path"]
    wav_path = file_path
    flac_path = file_path.with_suffix(".flac")

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

                seg = get_next_unchecked_segments(conn)
                if not seg:
                    logger.info("No unchecked segments to process.")
                    time.sleep(60)
                    continue

                segments = get_next_unchecked_segments(conn, batch_size=100)
                if not segments:
                    logger.info("No unchecked segments to process.")
                    time.sleep(60)
                    continue

                touched_file_ids = set()
                for seg in segments:
                    delete = check_segment_for_deletion(conn, seg["segment_id"])
                    if delete:
                        frame_count = seg["end_frame"] - seg["start_frame"]
                        success = False
                        if frame_count > 0:
                            success = overwrite_segment_with_zeros(
                                seg["file_path"],
                                seg["start_frame"],
                                frame_count,
                                seg["channel"],
                            )
                        if success:
                            logger.info(f"Zeroed segment {seg['segment_id']}.")
                        else:
                            logger.warning(
                                f"Failed to zero segment {seg['segment_id']}."
                            )

                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE sensos.audio_segments SET checked = TRUE, zeroed = %s WHERE id = %s",
                            (delete, seg["segment_id"]),
                        )
                        conn.commit()

                    if delete:
                        if is_file_fully_zeroed(conn, seg["file_id"]):
                            file_path = AUDIO_BASE / seg["file_path"]
                            if file_path.exists():
                                try:
                                    file_path.unlink()
                                    logger.info(
                                        f"Deleted fully zeroed file: {file_path}"
                                    )
                                except Exception as e:
                                    logger.error(
                                        f"Failed to delete zeroed file {file_path}: {e}"
                                    )
                            with conn.cursor() as cur:
                                mark_file_deleted(cur, seg["file_id"])
                                conn.commit()

                        touched_file_ids.add(seg["file_id"])

                    for file_id in touched_file_ids:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT file_path FROM sensos.audio_files WHERE id = %s AND deleted = FALSE",
                                (file_id,),
                            )
                            row = cur.fetchone()
                            if row:
                                seg = {
                                    "file_id": file_id,
                                    "file_path": row["file_path"],
                                }
                                compress_file_if_done(conn, seg)

        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
