#!/usr/bin/env python3

import os
import re
import logging
from pathlib import Path
import psycopg
import soundfile as sf
import numpy as np
from datetime import datetime

DB_PARAMS = (
    f"dbname={os.environ.get('POSTGRES_DB', 'postgres')} "
    f"user={os.environ.get('POSTGRES_USER', 'postgres')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'sensos')} "
    f"host={os.environ.get('DB_HOST', 'sensos-client-database')} "
    f"port={os.environ.get('DB_PORT', '5432')}"
)

AUDIO_BASE_PATH = Path("/audio_recordings")
TOP_N = int(os.environ.get("N_EXAMPLES", 3))  # top N per label
TOTAL_LIMIT = int(os.environ.get("TOTAL_LIMIT", 100))  # total max output
THRESHOLD = float(os.environ.get("SCORE_THRESHOLD", 0))

# Output to a timestamped directory
OUTPUT_BASE = Path("/audio_recordings/examples")
now = datetime.now()
dt_str = now.strftime("examples_%Y-%m-%d_%H%M")
OUTPUT_PATH = OUTPUT_BASE / dt_str

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("extract-segments")


def safe_filename(s):
    # Replace spaces with underscores, keep only alphanumerics, dash, underscore, and dot
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


def extract_and_write(abs_path, start_frame, end_frame, channel, out_path, sample_rate):
    try:
        with sf.SoundFile(str(abs_path), "r") as sf_file:
            sf_file.seek(start_frame)
            frames_to_read = end_frame - start_frame
            audio = sf_file.read(frames=frames_to_read, dtype="float32", always_2d=True)
            # Select only the segment's channel
            if audio.shape[1] > 1:
                audio = audio[:, channel].reshape(-1, 1)
            # Ensure output directory exists
            os.makedirs(out_path.parent, exist_ok=True)
            # Write to .wav file
            sf.write(str(out_path), audio, sample_rate, format="WAV", subtype="PCM_16")
        logger.info(f"Wrote {out_path}")
    except Exception as e:
        logger.error(f"Failed to write {out_path}: {e}")


def main():
    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM (
                    SELECT
                        b.label,
                        s.id AS segment_id,
                        s.file_id,
                        s.channel,
                        s.start_frame,
                        s.end_frame,
                        b.score,
                        ROW_NUMBER() OVER (PARTITION BY b.label ORDER BY b.score DESC) AS rn
                    FROM sensos.birdnet_scores b
                    JOIN sensos.audio_segments s ON b.segment_id = s.id
                    WHERE b.score >= %s
                      AND s.zeroed IS NOT TRUE
                ) sub
                WHERE rn <= %s
                ORDER BY score DESC
                LIMIT %s
                """,
                (THRESHOLD, TOP_N, TOTAL_LIMIT),
            )
            segments = cur.fetchall()

            logger.info(
                f"Extracting {len(segments)} segments (top {TOP_N} per label, global max {TOTAL_LIMIT})."
            )
            for row in segments:
                label, seg_id, file_id, channel, start_frame, end_frame, score, rn = row
                cur.execute(
                    """SELECT file_path, sample_rate, channels, format, subtype
                       FROM sensos.audio_files WHERE id = %s""",
                    (file_id,),
                )
                f = cur.fetchone()
                if not f:
                    logger.warning(f"File missing for segment {seg_id}")
                    continue
                file_path, sample_rate, channels, fmt, subtype = f
                abs_path = AUDIO_BASE_PATH / file_path
                base_name = f"{label}_{score:.3f}_{seg_id}.wav"
                out_name = safe_filename(base_name)
                out_path = OUTPUT_PATH / out_name

                logger.info(
                    f"Extracting: {abs_path} [ch {channel}, frames {start_frame}:{end_frame}] "
                    f"-> {out_path} (label={label}, score={score:.3f})"
                )
                extract_and_write(
                    abs_path, start_frame, end_frame, channel, out_path, sample_rate
                )


if __name__ == "__main__":
    main()
