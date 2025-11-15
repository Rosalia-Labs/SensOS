#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

"""
Multi-client audio catalog service for analytics server.
Processes audio files organized by client_id subdirectories.
Expected structure: /audio_recordings/<client_id>/queued/ and /audio_recordings/<client_id>/cataloged/
"""

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
EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg"}

DB_PARAMS = {
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
    "host": os.environ["DB_HOST"],
    "port": os.environ["DB_PORT"],
}


def extract_timestamp(path: Path) -> float:
    """Extract timestamp from filename or use file mtime."""
    name = path.name
    if name.startswith("sensos_"):
        try:
            stamp = name[len("sensos_") :].split(".")[0]
            dt = datetime.datetime.strptime(stamp, "%Y%m%dT%H%M%S")
            return dt.timestamp()
        except Exception as e:
            logging.warning(f"Timestamp parse failed for {name}: {e}")
    return path.stat().st_mtime


def get_client_dirs() -> list[tuple[int, Path, Path]]:
    """
    Scan ROOT for client directories and return list of (client_id, queued_dir, cataloged_dir).
    Client directories are named with numeric IDs.
    """
    client_dirs = []
    if not ROOT.exists():
        return client_dirs
    
    for entry in ROOT.iterdir():
        if not entry.is_dir():
            continue
        
        # Check if directory name is a number (client_id)
        try:
            client_id = int(entry.name)
        except ValueError:
            logging.warning(f"Skipping non-numeric directory: {entry.name}")
            continue
        
        queued = entry / "queued"
        cataloged = entry / "cataloged"
        other = entry / "other"
        
        # Ensure directories exist
        queued.mkdir(exist_ok=True)
        cataloged.mkdir(exist_ok=True)
        other.mkdir(exist_ok=True)
        
        client_dirs.append((client_id, queued, cataloged, other))
    
    return client_dirs


def move_and_cleanup(
    path: Path,
    destination_root: Path,
    reason: str,
    new_name: Optional[str] = None,
    cur=None,
    client_id: int = None,
):
    """
    Move a file from cataloged/ to a new location (e.g., queued/, other/), optionally delete DB entry.
    """
    cataloged_base = path.parents[0]
    while cataloged_base.name != "cataloged" and cataloged_base != Path("/"):
        cataloged_base = cataloged_base.parent
    
    if cataloged_base.name != "cataloged":
        logging.error(f"Could not find cataloged base for {path}")
        return None
    
    rel_path = path.relative_to(cataloged_base)
    dest_name = new_name if new_name else path.name
    dest_path = destination_root / rel_path.parent / dest_name

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest_path))
        logging.warning(
            f"Moved file to {destination_root.name}/: {rel_path} — {reason}"
        )

        if cur and client_id is not None:
            # Remove from database
            cur.execute(
                "DELETE FROM sensos.audio_files WHERE client_id = %s AND file_path = %s",
                (client_id, str(rel_path)),
            )
            logging.info(f"Deleted DB entry for moved file: client_id={client_id}, path={rel_path}")
        return dest_path
    except Exception as e:
        logging.error(f"Failed to move file {path} to {dest_path}: {e}")
        return None


def check_catalog(cur, client_id: int, cataloged: Path, other: Path):
    """Verify cataloged files match database records."""
    seen_paths = set()

    for path in cataloged.rglob("*"):
        if not path.is_file():
            continue

        rel_path = path.relative_to(cataloged).as_posix()
        seen_paths.add(rel_path)

        try:
            try:
                info = sf.info(path)
            except Exception as e:
                move_and_cleanup(
                    path, other / "cataloged", f"Unreadable by soundfile: {e}",
                    cur=cur, client_id=client_id
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
                    path, other / "cataloged", "Unrecognized file extension",
                    cur=cur, client_id=client_id
                )
                continue

            cur.execute(
                "SELECT 1 FROM sensos.audio_files WHERE client_id = %s AND file_path = %s",
                (client_id, rel_path)
            )
            in_db = cur.fetchone() is not None

            if path.suffix.lower() != actual_ext or not in_db:
                queued = cataloged.parent / "queued"
                move_and_cleanup(
                    path,
                    queued,
                    "Extension mismatch or missing DB entry",
                    new_name=path.stem + actual_ext,
                    cur=cur,
                    client_id=client_id,
                )

        except Exception as e:
            logging.error(f"Error handling file {path}: {e}")
            cur.connection.rollback()

    # Check for missing files in DB
    cur.execute(
        "SELECT file_path FROM sensos.audio_files WHERE client_id = %s AND deleted = FALSE",
        (client_id,)
    )
    for (db_path,) in cur:
        if db_path not in seen_paths:
            cur.execute(
                """UPDATE sensos.audio_files 
                   SET deleted = TRUE, deleted_at = NOW() 
                   WHERE client_id = %s AND file_path = %s""",
                (client_id, db_path),
            )
            logging.warning(f"Marked missing file as deleted: client_id={client_id}, path={db_path}")


def process_files(cur, client_id: int, queued: Path, cataloged: Path, other: Path) -> int:
    """Process queued audio files for a specific client."""
    count = 0
    for path in queued.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in EXTENSIONS:
            rel_path = path.relative_to(queued)
            dest_path = other / "queued" / rel_path
            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest_path))
                logging.warning(f"Moved unknown file type from queued/: {rel_path}")
            except Exception as e:
                logging.error(f"Failed to move unknown file from queued/: {rel_path} — {e}")
            continue

        if is_stable(path):
            try:
                process_file(cur, client_id, path, queued, cataloged)
                count += 1
            except Exception as e:
                logging.error(f"Unhandled error processing {path}: {e}")
        else:
            logging.debug(f"Skipped unstable file: {path}")

    return count


def process_file(cursor, client_id: int, path: Path, queued: Path, cataloged: Path):
    """Process a single audio file for a specific client."""
    rel_input = path.relative_to(queued)
    output_name = path.stem + ".flac"
    new_path = cataloged / rel_input.parent / output_name
    new_rel = new_path.relative_to(cataloged).as_posix()
    tmp_path = None

    # Check if already processed
    cursor.execute(
        "SELECT 1 FROM sensos.audio_files WHERE client_id = %s AND file_path = %s",
        (client_id, new_rel)
    )
    if cursor.fetchone():
        logging.warning(f"Already processed: client_id={client_id}, path={new_rel}")
        try:
            os.remove(path)
            logging.info(f"Removed already-processed input file from queued: {path}")
        except Exception as e:
            logging.error(f"Failed to remove already-processed input file: {path} — {e}")
        return

    try:
        # Read original metadata
        try:
            info = sf.info(path)
        except Exception as e:
            logging.error(f"Could not read metadata from {path}: {e}")
            return

        timestamp = extract_timestamp(path)

        # Convert to FLAC
        tmp_path = new_path.with_suffix(".tmp")
        data, sr = sf.read(path, always_2d=True)
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(tmp_path, data, sr, format="FLAC")
        tmp_path.replace(new_path)

        # Get final FLAC metadata
        try:
            final_info = sf.info(new_path)
        except Exception as e:
            logging.error(f"Could not read FLAC metadata from {new_path}: {e}")
            return

        # Remove original
        os.remove(path)

        # Insert into database with client_id
        cursor.execute(
            """
            INSERT INTO sensos.audio_files (
                client_id, file_path, frames, channels, sample_rate,
                format, subtype, capture_timestamp
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, to_timestamp(%s));
            """,
            (
                client_id,
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
        logging.info(f"Processed and recorded: client_id={client_id}, path={new_rel}")

    except Exception as e:
        logging.error(f"Failed processing {path}: {e}")
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        cursor.connection.rollback()


def is_stable(path: Path, threshold: float = 2.0) -> bool:
    """Return True if the file has not been modified in the last `threshold` seconds."""
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return False
    return (time.time() - mtime) > threshold


def wait_for_db(max_retries=30, delay=5):
    """Wait for database to be ready."""
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


def remove_deleted_files(cur, client_id: int, cataloged: Path):
    """Delete files from disk that are marked as deleted in the database."""
    cur.execute(
        "SELECT file_path FROM sensos.audio_files WHERE client_id = %s AND deleted = TRUE",
        (client_id,)
    )
    count = 0
    for row in cur.fetchall():
        file_path = cataloged / row[0]
        if file_path.exists():
            try:
                file_path.unlink()
                logging.info(f"Removed file marked as deleted: {file_path}")
                count += 1
            except Exception as e:
                logging.error(f"Failed to remove deleted file {file_path}: {e}")
    if count:
        logging.info(f"Removed {count} deleted files from disk for client_id={client_id}")


def main():
    """Main processing loop."""
    wait_for_db()
    
    logging.info("Starting multi-client audio cataloging service...")
    
    while True:
        try:
            client_dirs = get_client_dirs()
            
            if not client_dirs:
                logging.info("No client directories found. Waiting...")
                time.sleep(60)
                continue
            
            with psycopg.connect(**DB_PARAMS) as conn:
                for client_id, queued, cataloged, other in client_dirs:
                    with conn.cursor() as cur:
                        # Verify client exists in database
                        cur.execute("SELECT 1 FROM sensos.clients WHERE id = %s", (client_id,))
                        if not cur.fetchone():
                            logging.warning(
                                f"Client ID {client_id} not found in database. Skipping."
                            )
                            continue
                        
                        logging.info(f"Processing files for client_id={client_id}")
                        remove_deleted_files(cur, client_id, cataloged)
                        count = process_files(cur, client_id, queued, cataloged, other)
                        check_catalog(cur, client_id, cataloged, other)
                        logging.info(f"Processed {count} new files for client_id={client_id}")
                        conn.commit()
            
            logging.info("Sleeping 60s before next check.")
            time.sleep(60)
            
        except Exception as e:
            logging.error(f"Error in main loop: {e}", exc_info=True)
            time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Interrupted. Exiting.")
