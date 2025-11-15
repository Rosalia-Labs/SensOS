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
EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg"}

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
    
    # Extensions for spatial and vector data
    cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    
    # Locations table - primary identifier for data sources
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.locations (
            id SERIAL PRIMARY KEY,
            location_name TEXT UNIQUE NOT NULL,
            latitude DOUBLE PRECISION NOT NULL,
            longitude DOUBLE PRECISION NOT NULL,
            elevation_m REAL,
            notes TEXT,
            client_uuid UUID,
            client_wg_ip INET,
            client_hostname TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_import_at TIMESTAMPTZ
        );"""
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS locations_name_idx
        ON sensos.locations(location_name);
        CREATE INDEX IF NOT EXISTS locations_coords_idx
        ON sensos.locations(latitude, longitude);
        """
    )
    
    # Audio files with location tracking
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.audio_files (
            id SERIAL PRIMARY KEY,
            location_id INTEGER NOT NULL REFERENCES sensos.locations(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL,
            frames BIGINT,
            channels INTEGER,
            sample_rate INTEGER,
            format TEXT,   
            subtype TEXT,
            capture_timestamp TIMESTAMPTZ,
            cataloged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            UNIQUE(location_id, file_path)
        );"""
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS audio_files_location_id_idx
        ON sensos.audio_files(location_id);
        CREATE INDEX IF NOT EXISTS audio_files_file_path_idx
        ON sensos.audio_files(file_path);
        CREATE INDEX IF NOT EXISTS audio_files_capture_timestamp_idx
        ON sensos.audio_files(capture_timestamp);
        CREATE INDEX IF NOT EXISTS audio_files_location_capture_idx
        ON sensos.audio_files(location_id, capture_timestamp);
        CREATE INDEX IF NOT EXISTS audio_files_capture_channels_idx
        ON sensos.audio_files(capture_timestamp, channels);
        CREATE INDEX IF NOT EXISTS audio_files_deleted_idx
        ON sensos.audio_files(deleted);
        """
    )

def get_location_dirs() -> list[tuple[str, Path, Path, Path]]:
    """
    Return a list of (location_name, base_dir, queued_dir, cataloged_dir, other_dir)
    for each location directory under ROOT.
    """
    results = []
    if not ROOT.exists():
        return results
    for entry in ROOT.iterdir():
        if not entry.is_dir():
            continue
        location_name = entry.name
        base_dir = entry
        queued = entry / "queued"
        cataloged = entry / "cataloged"
        other = entry / "other"
        queued.mkdir(exist_ok=True)
        cataloged.mkdir(exist_ok=True)
        other.mkdir(exist_ok=True)
        results.append((location_name, base_dir, queued, cataloged, other))
    return results


def get_location_id(cur, location_name: str) -> Optional[int]:
    cur.execute(
        "SELECT id FROM sensos.locations WHERE location_name = %s",
        (location_name,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def move_and_cleanup(
    path: Path,
    destination_root: Path,
    reason: str,
    new_name: Optional[str] = None,
    cur=None,
    location_id: Optional[int] = None,
    base_dir: Optional[Path] = None,
):
    """
    Move a file from cataloged/ to a new location (e.g., queued/, other/), optionally delete DB entry.
    The database file_path is stored relative to the location base directory (e.g., 'cataloged/YYYY/MM/DD/file.flac').
    """
    try:
        if base_dir is None:
            raise ValueError("base_dir is required for move_and_cleanup")
        cataloged_dir = base_dir / "cataloged"
        rel_under_cataloged = path.relative_to(cataloged_dir)
        dest_name = new_name if new_name else path.name
        dest_path = destination_root / rel_under_cataloged.parent / dest_name

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest_path))
        logging.warning(
            f"Moved file to {destination_root.name}/: {rel_under_cataloged} — {reason}"
        )

        # Remove DB entry based on location and relative path from base_dir
        if cur is not None and location_id is not None:
            rel_from_base = (Path("cataloged") / rel_under_cataloged).as_posix()
            cur.execute(
                "DELETE FROM sensos.audio_files WHERE location_id = %s AND file_path = %s",
                (location_id, rel_from_base),
            )
            logging.info(
                f"Deleted DB entry for moved file: location_id={location_id}, path={rel_from_base}"
            )
        return dest_path
    except Exception as e:
        logging.error(f"Failed to move file {path} to {destination_root}: {e}")
        return None


def check_catalog(cur):
    """
    For each location directory, verify cataloged files match database records.
    """
    for location_name, base_dir, queued, cataloged, other in get_location_dirs():
        location_id = get_location_id(cur, location_name)
        if location_id is None:
            logging.warning(
                f"Location '{location_name}' not found in database; skipping catalog check for this directory."
            )
            continue

        seen_paths = set()

        for path in cataloged.rglob("*"):
            if not path.is_file():
                continue

            # DB file_path is relative to base_dir, e.g., 'cataloged/YYYY/MM/DD/file.flac'
            rel_from_base = path.relative_to(base_dir).as_posix()
            seen_paths.add(rel_from_base)

            try:
                try:
                    info = sf.info(path)
                except Exception as e:
                    move_and_cleanup(
                        path,
                        other / "cataloged",
                        f"Unreadable by soundfile: {e}",
                        cur=cur,
                        location_id=location_id,
                        base_dir=base_dir,
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
                        path,
                        other / "cataloged",
                        "Unrecognized file extension",
                        cur=cur,
                        location_id=location_id,
                        base_dir=base_dir,
                    )
                    continue

                cur.execute(
                    "SELECT 1 FROM sensos.audio_files WHERE location_id = %s AND file_path = %s",
                    (location_id, rel_from_base),
                )
                in_db = cur.fetchone() is not None

                if path.suffix.lower() != actual_ext or not in_db:
                    move_and_cleanup(
                        path,
                        queued,
                        "Extension mismatch or missing DB entry",
                        new_name=path.stem + actual_ext,
                        cur=cur,
                        location_id=location_id,
                        base_dir=base_dir,
                    )

            except Exception as e:
                logging.error(f"Error handling file {path}: {e}")
                cur.connection.rollback()

        # Mark missing files as deleted
        cur.execute(
            "SELECT file_path FROM sensos.audio_files WHERE location_id = %s AND deleted = FALSE",
            (location_id,),
        )
        for (db_path,) in cur:
            if db_path not in seen_paths:
                cur.execute(
                    """
                    UPDATE sensos.audio_files
                    SET deleted = TRUE, deleted_at = NOW()
                    WHERE location_id = %s AND file_path = %s
                    """,
                    (location_id, db_path),
                )
                logging.warning(
                    f"Marked missing file as deleted in DB: location_id={location_id}, path={db_path}"
                )


def enrich_missing_metadata(cur):
    """
    For each location, find cataloged files that are registered without audio
    metadata and update frames/channels/sample_rate/format/subtype from disk.
    """
    for location_name, base_dir, queued, cataloged, other in get_location_dirs():
        location_id = get_location_id(cur, location_name)
        if location_id is None:
            continue
        cur.execute(
            """
            SELECT file_path FROM sensos.audio_files
            WHERE location_id = %s AND deleted = FALSE
              AND (frames IS NULL OR channels IS NULL OR sample_rate IS NULL OR format IS NULL OR subtype IS NULL)
            """,
            (location_id,),
        )
        rows = cur.fetchall()
        if not rows:
            continue
        updated = 0
        for (rel_path,) in rows:
            p = base_dir / rel_path
            try:
                info = sf.info(p)
                cur.execute(
                    """
                    UPDATE sensos.audio_files
                    SET frames = %s,
                        channels = %s,
                        sample_rate = %s,
                        format = %s,
                        subtype = %s
                    WHERE location_id = %s AND file_path = %s
                    """,
                    (
                        info.frames,
                        info.channels,
                        info.samplerate,
                        info.format,
                        info.subtype,
                        location_id,
                        rel_path,
                    ),
                )
                updated += 1
            except Exception as e:
                logging.warning(
                    f"Failed to enrich metadata for {p}: {e}"
                )
        if updated:
            logging.info(
                f"Enriched metadata for {updated} files at location '{location_name}'"
            )

def process_files(cur) -> int:
    """
    Process queued files for all known locations. Returns total processed count.
    """
    total = 0
    for location_name, base_dir, queued, cataloged, other in get_location_dirs():
        location_id = get_location_id(cur, location_name)
        if location_id is None:
            logging.warning(
                f"Location '{location_name}' not found in database; skipping processing for this directory."
            )
            continue

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
                    logging.warning(
                        f"Moved unknown file type from queued/: {rel_path} (location={location_name})"
                    )
                except Exception as e:
                    logging.error(
                        f"Failed to move unknown file from queued/: {rel_path} — {e}"
                    )
                continue

            if is_stable(path):
                try:
                    process_file(cur, location_id, base_dir, queued, cataloged, path)
                    count += 1
                except Exception as e:
                    logging.error(f"Unhandled error processing {path}: {e}")
            else:
                logging.debug(f"Skipped unstable file: {path}")

        logging.info(f"Processed {count} new files for location '{location_name}'")
        total += count
    return total


def process_file(cursor, location_id: int, base_dir: Path, queued: Path, cataloged: Path, path: Path):
    """
    Process a single file for a given location. Converts to FLAC in cataloged tree
    and inserts/updates the database with location_id and relative file_path from base_dir.
    """
    rel_input = path.relative_to(queued)
    output_name = path.stem + ".flac"
    new_path = cataloged / rel_input.parent / output_name
    # Relative path from the location base directory
    new_rel = new_path.relative_to(base_dir).as_posix()
    tmp_path = None

    cursor.execute(
        "SELECT 1 FROM sensos.audio_files WHERE location_id = %s AND file_path = %s",
        (location_id, new_rel),
    )
    if cursor.fetchone():
        logging.warning(
            f"Already processed for location_id={location_id}: {new_rel}"
        )
        try:
            os.remove(path)
            logging.info(f"Removed already-processed input file from queued: {path}")
        except Exception as e:
            logging.error(
                f"Failed to remove already-processed input file: {path} — {e}"
            )
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

        # Insert into database with location_id
        cursor.execute(
            """
            INSERT INTO sensos.audio_files (
                location_id, file_path, frames, channels, sample_rate,
                format, subtype, capture_timestamp
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, to_timestamp(%s));
            """,
            (
                location_id,
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
        logging.info(
            f"Processed and recorded for location_id={location_id}: {new_rel}"
        )

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


def remove_deleted_files(cur):
    """
    For each known location, delete files from disk that are marked as deleted in the database.
    """
    for location_name, base_dir, queued, cataloged, other in get_location_dirs():
        location_id = get_location_id(cur, location_name)
        if location_id is None:
            logging.warning(
                f"Location '{location_name}' not found in database; skipping removal of deleted files."
            )
            continue
        cur.execute(
            "SELECT file_path FROM sensos.audio_files WHERE location_id = %s AND deleted = TRUE",
            (location_id,),
        )
        count = 0
        for row in cur.fetchall():
            file_path = base_dir / row[0]
            if file_path.exists():
                try:
                    file_path.unlink()
                    logging.info(f"Removed file marked as deleted: {file_path}")
                    count += 1
                except Exception as e:
                    logging.error(f"Failed to remove deleted file {file_path}: {e}")
        if count:
            logging.info(
                f"Removed {count} deleted files from disk for location '{location_name}'"
            )


def main():
    wait_for_db()
    with psycopg.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            ensure_schema(cur)
            remove_deleted_files(cur)
            processed = process_files(cur)
            check_catalog(cur)
            conn.commit()
            logging.info(f"Initial sweep processed {processed} files across locations")

        while True:
            with conn.cursor() as cur:
                count = process_files(cur)
                logging.info(
                    f"Processed {count} new files from queued/ across all locations"
                )
                check_catalog(cur)
                enrich_missing_metadata(cur)
                conn.commit()
            logging.info("Sleeping 60s before next check.")
            time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Interrupted. Exiting.")
