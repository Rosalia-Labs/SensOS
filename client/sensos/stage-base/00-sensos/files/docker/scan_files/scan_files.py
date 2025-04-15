#!/usr/bin/env python3

import os
import time
import shutil
import logging
import datetime
from pathlib import Path

import numpy as np
import soundfile as sf
import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

ROOT = Path("/mnt/audio_recordings")
QUEUED = ROOT / "queued"
CATALOGED = ROOT / "cataloged"
EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg"}

DB_PARAMS = (
    f"dbname={os.environ['POSTGRES_DB']} "
    f"user={os.environ['POSTGRES_USER']} "
    f"password={os.environ['POSTGRES_PASSWORD']} "
    f"host={os.environ['DB_HOST']} "
    f"port={os.environ['DB_PORT']}"
)


def extract_timestamp(path: Path) -> float:
    name = path.name
    if name.startswith("sensos_"):
        try:
            stamp = name[len("sensos_") :].split(".")[0]
            dt = datetime.datetime.strptime(stamp, "%Y%m%dT%H%M%S")
            return dt.timestamp()
        except Exception as e:
            logging.warning(f"Timestamp parse failed for {name}: {e}")
    return path.stat().st_mtime


def ensure_schema(cursor):
    cursor.execute("CREATE SCHEMA IF NOT EXISTS sensos;")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.audio_files (
            id SERIAL PRIMARY KEY,
            file_path TEXT,
            cataloged_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS audio_files_file_path_index
        ON sensos.audio_files(file_path);
        """
    )


def already_in_db(cursor, rel_path: str) -> bool:
    cursor.execute("SELECT 1 FROM sensos.audio_files WHERE file_path = %s", (rel_path,))
    return cursor.fetchone() is not None


def process_file(cursor, path: Path):
    rel_input = path.relative_to(QUEUED)
    output_name = path.stem + ".flac"
    new_path = CATALOGED / rel_input.parent / output_name
    new_rel = new_path.relative_to(ROOT).as_posix()

    try:
        tmp_path = new_path.with_suffix(".tmp")
        data, sr = sf.read(path, always_2d=True)
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(tmp_path, data, sr, format="FLAC")
        tmp_path.replace(new_path)  # atomic move/overwrite

        os.remove(path)

        # Only insert file_path
        cursor.execute(
            """
            INSERT INTO sensos.audio_files (file_path)
            VALUES (%s);
            """,
            (new_rel,),
        )
        logging.info(f"Processed and recorded {new_rel}")

    except Exception as e:
        logging.error(f"Failed processing {path}: {e}")
        if "tmp_path" in locals() and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        cursor.connection.rollback()


def restore_untracked_processed_files(cursor):
    restored = 0
    deleted = 0

    for path in CATALOGED.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in EXTENSIONS:
            logging.warning(f"Non-soundfile {path} found in audio directory.")
            continue

        rel_path = path.relative_to(ROOT).as_posix()

        # Check if it's already in the database
        cursor.execute(
            "SELECT 1 FROM sensos.audio_files WHERE file_path = %s", (rel_path,)
        )
        if cursor.fetchone():
            continue  # Already recorded

        # Check for matching file in queued dir (any extension)
        already_queued = any(
            (QUEUED / path.relative_to(CATALOGED)).with_suffix(ext).exists()
            for ext in EXTENSIONS
        )

        if already_queued:
            try:
                path.unlink()
                logging.warning(f"Deleted {rel_path} due to queued version existing")
                deleted += 1
            except Exception as e:
                logging.error(f"Failed to delete {rel_path}: {e}")
        else:
            dest_path = QUEUED / path.relative_to(CATALOGED)
            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(path, dest_path)
                logging.warning(f"Moved untracked file back to queued: {rel_path}")
                restored += 1
            except Exception as e:
                logging.error(f"Failed to restore {rel_path}: {e}")

    if restored or deleted:
        logging.info(f"Restored {restored} and deleted {deleted} files from cataloged/")


def main():
    with psycopg.connect(DB_PARAMS) as conn:
        with conn.cursor() as cur:
            ensure_schema(cur)
            restore_untracked_processed_files(cur)
            conn.commit()

        while True:
            count = 0
            with conn.cursor() as cur:
                for path in QUEUED.rglob("*"):
                    if path.is_file() and path.suffix.lower() in EXTENSIONS:
                        try:
                            process_file(cur, path)
                            count += 1
                        except Exception as e:
                            logging.error(f"Unhandled error processing {path}: {e}")
                            conn.rollback()
                conn.commit()
            if count == 0:
                logging.info("No new files found. Sleeping 60s.")
                time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Interrupted. Exiting.")
