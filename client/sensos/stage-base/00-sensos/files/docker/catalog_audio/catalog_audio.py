#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

import os
import time
import shutil
import logging
import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import subprocess
import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

ROOT = Path("/audio_recordings")
QUEUED = ROOT / "queued"
CATALOGED = ROOT / "cataloged"
EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg"}
OTHER = ROOT / "other"

DB_PARAMS = {
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
    "host": os.environ["DB_HOST"],
    "port": os.environ["DB_PORT"],
}


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
    db_name = DB_PARAMS["dbname"]
    cursor.execute("CREATE SCHEMA IF NOT EXISTS sensos;")
    cursor.execute(f"ALTER DATABASE {db_name} SET search_path TO sensos, public;")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.audio_files (
            id SERIAL PRIMARY KEY,
            file_path TEXT UNIQUE,
            frames BIGINT,
            channels INTEGER,
            sample_rate INTEGER,
            format TEXT,   
            subtype TEXT,
            capture_timestamp TIMESTAMPTZ,
            cataloged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ
        );"""
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS audio_files_file_path_index
        ON sensos.audio_files(file_path);
        CREATE INDEX IF NOT EXISTS audio_files_capture_timestamp_index
        ON sensos.audio_files(capture_timestamp);
        CREATE INDEX IF NOT EXISTS audio_files_capture_channels_idx
        ON sensos.audio_files(capture_timestamp, channels);
        CREATE INDEX IF NOT EXISTS audio_files_deleted_idx
        ON sensos.audio_files(deleted);
        """
    )


def already_in_db(cursor, rel_path: str) -> bool:
    cursor.execute("SELECT 1 FROM sensos.audio_files WHERE file_path = %s", (rel_path,))
    return cursor.fetchone() is not None


def move_and_cleanup(
    path: Path,
    destination_root: Path,
    reason: str,
    new_name: Optional[str] = None,
    cur=None,
):
    """
    Move a file from cataloged/ to a new location (e.g., queued/, other/), optionally delete DB entry.
    """
    rel_path = path.relative_to(CATALOGED)
    dest_name = new_name if new_name else path.name
    dest_path = destination_root / rel_path.parent / dest_name

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest_path))
        logging.warning(
            f"Moved file to {destination_root.name}/: {rel_path} — {reason}"
        )

        if cur:
            rel_path_str = (ROOT / "cataloged" / rel_path).as_posix()
            cur.execute(
                "DELETE FROM sensos.audio_files WHERE file_path = %s", (rel_path_str,)
            )
            logging.info(f"Deleted DB entry for moved file: {rel_path_str}")
        return dest_path
    except Exception as e:
        logging.error(f"Failed to move file {path} to {dest_path}: {e}")
        return None


def move_queued_to_other(path: Path, reason: str) -> Optional[Path]:
    rel_path = path.relative_to(QUEUED)
    dest_path = OTHER / "queued" / rel_path
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest_path))
        logging.warning(f"Moved queued file to other/: {rel_path} — {reason}")
        return dest_path
    except Exception as e:
        logging.error(f"Failed to quarantine queued file {path}: {e}")
        return None


def check_catalog(cur):
    seen_paths = set()

    for path in CATALOGED.rglob("*"):
        if not path.is_file():
            continue

        rel_path = path.relative_to(ROOT).as_posix()
        seen_paths.add(rel_path)

        try:
            try:
                info = sf.info(path)
            except Exception as e:
                move_and_cleanup(
                    path, OTHER / "cataloged", f"Unreadable by soundfile: {e}", cur=cur
                )
                continue

            actual_ext = {
                "FLAC": ".flac",
                "WAV": ".wav",
                "OGG": ".ogg",
                "MP3": ".mp3",
            }.get(info.format.upper())

            if actual_ext is None:
                move_and_cleanup(
                    path, OTHER / "cataloged", "Unrecognized file extension", cur=cur
                )
                continue

            cur.execute(
                "SELECT 1 FROM sensos.audio_files WHERE file_path = %s", (rel_path,)
            )
            in_db = cur.fetchone() is not None

            if path.suffix.lower() != actual_ext or not in_db:
                move_and_cleanup(
                    path,
                    QUEUED,
                    "Extension mismatch or missing DB entry",
                    new_name=path.stem + actual_ext,
                    cur=cur,
                )

        except Exception as e:
            logging.error(f"Error handling file {path}: {e}")
            cur.connection.rollback()

    cur.execute("SELECT file_path FROM sensos.audio_files WHERE deleted = FALSE")
    for (db_path,) in cur:
        if db_path not in seen_paths:
            cur.execute(
                "UPDATE sensos.audio_files SET deleted = TRUE, deleted_at = NOW() WHERE file_path = %s",
                (db_path,),
            )
            logging.warning(f"Marked missing file as deleted in DB: {db_path}")


def process_files(cur) -> int:
    count = 0
    for path in QUEUED.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in EXTENSIONS:
            rel_path = path.relative_to(ROOT)
            dest_path = OTHER / "queued" / rel_path
            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest_path))
                logging.warning(f"Moved unknown file type from queued/: {rel_path}")
            except Exception as e:
                logging.error(
                    f"Failed to move unknown file from queued/: {rel_path} — {e}"
                )
            continue

        if is_stable(path):
            try:
                process_file(cur, path)
                count += 1
            except Exception as e:
                logging.error(f"Unhandled error processing {path}: {e}")
        else:
            logging.info(f"Skipped unstable file: {path}")

    return count


def process_file(cursor, path: Path):
    rel_input = path.relative_to(QUEUED)
    output_name = path.stem + ".flac"
    new_path = CATALOGED / rel_input.parent / output_name
    new_rel = new_path.relative_to(ROOT).as_posix()
    tmp_path = None

    cursor.execute("SELECT 1 FROM sensos.audio_files WHERE file_path = %s", (new_rel,))
    if cursor.fetchone():
        logging.warning(f"Already processed: {new_rel}")
        try:
            os.remove(path)
            logging.info(f"Removed already-processed input file from queued: {path}")
        except Exception as e:
            logging.error(
                f"Failed to remove already-processed input file: {path} — {e}"
            )
        return

    try:
        try:
            info = sf.info(path)
        except Exception as e:
            logging.error(f"Could not read metadata from {path}: {e}")
            move_queued_to_other(path, f"Unreadable by soundfile: {e}")
            return

        timestamp = extract_timestamp(path)

        tmp_path = new_path.with_suffix(".tmp")
        data, sr = sf.read(path, always_2d=True)
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(tmp_path, data, sr, format="FLAC")
        tmp_path.replace(new_path)

        try:
            final_info = sf.info(new_path)
        except Exception as e:
            logging.error(f"Could not read FLAC metadata from {new_path}: {e}")
            try:
                move_and_cleanup(
                    new_path,
                    OTHER / "cataloged",
                    f"Unreadable converted FLAC: {e}",
                )
            except Exception:
                pass
            move_queued_to_other(path, "Converted output unreadable")
            return

        os.remove(path)

        cursor.execute(
            """
            INSERT INTO sensos.audio_files (
                file_path, frames, channels, sample_rate,
                format, subtype, capture_timestamp
            )
            VALUES (%s, %s, %s, %s, %s, %s, to_timestamp(%s));
            """,
            (
                new_rel,
                final_info.frames,
                final_info.channels,
                final_info.samplerate,
                final_info.format,
                final_info.subtype,
                timestamp,
            ),
        )
        cursor.connection.commit()
        logging.info(f"Processed and recorded {new_rel}")

    except Exception as e:
        logging.error(f"Failed processing {path}: {e}")
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        cursor.connection.rollback()


def is_stable(path: Path, threshold: float = 2.0) -> bool:
    """
    Return True if the file has not been modified in the last `threshold` seconds.
    """
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return False
    return (time.time() - mtime) > threshold


def wait_for_db(max_retries=30, delay=5):
    for i in range(max_retries):
        try:
            with psycopg.connect(**DB_PARAMS) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            logging.info("Database is ready.")
            return
        except Exception as e:
            logging.warning(f"Database not ready, retrying ({i+1}/{max_retries}): {e}")
            time.sleep(delay)
    raise RuntimeError("Database not ready after multiple attempts.")


def remove_deleted_files(cur, root=ROOT):
    """
    Delete files from disk that are marked as deleted in the database,
    if they still exist. Log each deletion.
    """
    cur.execute("SELECT file_path FROM sensos.audio_files WHERE deleted = TRUE")
    count = 0
    for row in cur.fetchall():
        file_path = root / row[0]
        if file_path.exists():
            try:
                file_path.unlink()
                logging.info(f"Removed file marked as deleted: {file_path}")
                count += 1
            except Exception as e:
                logging.error(f"Failed to remove deleted file {file_path}: {e}")
    if count:
        logging.info(f"Removed {count} deleted files from disk")


def main():
    wait_for_db()
    with psycopg.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            ensure_schema(cur)
            remove_deleted_files(cur)
            process_files(cur)
            check_catalog(cur)
            conn.commit()

        while True:
            with conn.cursor() as cur:
                count = process_files(cur)
                logging.info(f"Processed {count} new files from queued/")
            logging.info("Sleeping 60s before next check.")
            time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Interrupted. Exiting.")
