#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

import os
import re
import sys
import time
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import List

import numpy as np
import soundfile as sf
import tflite_runtime.interpreter as tflite

sys.path.append("/sensos/lib")
from utils import create_dir, read_kv_config, setup_logging  # noqa: E402

INPUT_ROOT = Path("/sensos/data/audio_recordings/queued")
OUTPUT_ROOT = Path("/sensos/data/audio_recordings/processed")
STATE_ROOT = Path("/sensos/data/birdnet")
DB_PATH = STATE_ROOT / "birdnet.db"
MODEL_ROOT = Path("/sensos/birdnet/BirdNET_v2.4_tflite")
MODEL_PATH = MODEL_ROOT / "audio-model.tflite"
LABELS_PATH = MODEL_ROOT / "labels" / "en_us.txt"
LOCATION_CONF = Path("/sensos/etc/location.conf")

WINDOW_SEC = 3
STRIDE_SEC = 1
SAMPLE_RATE = 48000
WINDOW_FRAMES = WINDOW_SEC * SAMPLE_RATE
STRIDE_FRAMES = STRIDE_SEC * SAMPLE_RATE
MIN_FILE_AGE_SEC = int(os.environ.get("BIRDNET_MIN_FILE_AGE_SEC", "15"))
FILE_STABLE_SEC = int(os.environ.get("BIRDNET_FILE_STABLE_SEC", "30"))
IDLE_SLEEP_SEC = int(os.environ.get("BIRDNET_IDLE_SLEEP_SEC", "60"))
ERROR_SLEEP_SEC = int(os.environ.get("BIRDNET_ERROR_SLEEP_SEC", "10"))


@dataclass
class BirdNETModel:
    interpreter: tflite.Interpreter
    input_details: list
    output_details: list
    labels: List[str]


@dataclass
class Detection:
    window_index: int
    start_frame: int
    end_frame: int
    label: str
    score: float


@dataclass
class LabelRun:
    run_index: int
    start_frame: int
    end_frame: int
    label: str
    peak_score: float


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_birdnet_model(model_path: Path, labels_path: Path) -> BirdNETModel:
    interpreter = tflite.Interpreter(model_path=str(model_path))
    interpreter.allocate_tensors()
    with labels_path.open("r", encoding="utf-8") as f:
        labels = [
            f"{common} ({sci})" if "_" in line else line.strip()
            for line in f.readlines()
            for sci, common in [line.strip().split("_", 1)]
        ]
    return BirdNETModel(
        interpreter=interpreter,
        input_details=interpreter.get_input_details(),
        output_details=interpreter.get_output_details(),
        labels=labels,
    )


def flat_sigmoid(x: np.ndarray, sensitivity: float = -1, bias: float = 1.0) -> np.ndarray:
    return 1 / (1.0 + np.exp(sensitivity * np.clip((x + (bias - 1.0) * 10.0), -20, 20)))


def scale_by_max_value(audio: np.ndarray) -> np.ndarray:
    max_val = np.max(np.abs(audio))
    if max_val == 0:
        return np.zeros_like(audio, dtype=np.float32)
    scale = max_val * (32768.0 / 32767.0)
    return (audio / scale).astype(np.float32)


def invoke_birdnet_top_label(audio: np.ndarray, model: BirdNETModel) -> tuple[str, float]:
    input_data = np.expand_dims(audio, axis=0).astype(np.float32)
    model.interpreter.set_tensor(model.input_details[0]["index"], input_data)
    model.interpreter.invoke()
    scores = model.interpreter.get_tensor(model.output_details[0]["index"])
    scores_flat = flat_sigmoid(scores.flatten())
    top_index = int(np.argmax(scores_flat))
    return model.labels[top_index], float(scores_flat[top_index])


def ensure_runtime_dirs() -> None:
    create_dir(STATE_ROOT, "sensos-admin", "sensos-data", 0o2775)
    create_dir(OUTPUT_ROOT, "sensos-admin", "sensos-data", 0o2775)


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_files (
            source_path TEXT PRIMARY KEY,
            sample_rate INTEGER,
            channels INTEGER,
            frames INTEGER,
            started_at TEXT NOT NULL,
            processed_at TEXT,
            status TEXT NOT NULL,
            error TEXT,
            output_dir TEXT,
            deleted_source INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT NOT NULL,
            window_index INTEGER NOT NULL,
            start_frame INTEGER NOT NULL,
            end_frame INTEGER NOT NULL,
            start_sec REAL NOT NULL,
            end_sec REAL NOT NULL,
            top_label TEXT NOT NULL,
            top_score REAL NOT NULL,
            UNIQUE (source_path, window_index)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS flac_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT NOT NULL,
            run_index INTEGER NOT NULL,
            label TEXT NOT NULL,
            start_frame INTEGER NOT NULL,
            end_frame INTEGER NOT NULL,
            start_sec REAL NOT NULL,
            end_sec REAL NOT NULL,
            peak_score REAL NOT NULL,
            flac_path TEXT NOT NULL,
            UNIQUE (source_path, run_index)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_detections_source ON detections (source_path, window_index)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_flac_runs_source ON flac_runs (source_path, run_index)"
    )
    conn.commit()
    return conn


def find_next_wav() -> Path | None:
    if not INPUT_ROOT.exists():
        return None
    now = time.time()
    candidates = []
    for path in INPUT_ROOT.rglob("*.wav"):
        try:
            age = now - path.stat().st_mtime
        except FileNotFoundError:
            continue
        if age >= MIN_FILE_AGE_SEC:
            candidates.append(path)
    return min(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def is_file_stable(path: Path) -> bool:
    try:
        first = path.stat()
    except FileNotFoundError:
        return False

    if (time.time() - first.st_mtime) < MIN_FILE_AGE_SEC:
        return False

    time.sleep(FILE_STABLE_SEC)

    try:
        second = path.stat()
    except FileNotFoundError:
        return False

    return (
        first.st_size == second.st_size
        and first.st_mtime == second.st_mtime
        and (time.time() - second.st_mtime) >= MIN_FILE_AGE_SEC
    )


def relative_source(path: Path) -> str:
    return path.relative_to(INPUT_ROOT.parent).as_posix()


def sanitize_label(label: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip()).strip("._-")
    return slug or "unknown"


def label_output_dir(source_path: Path, label: str) -> Path:
    rel = source_path.relative_to(INPUT_ROOT)
    return OUTPUT_ROOT / rel.parent / sanitize_label(label)


def format_coord(value: str | None, positive: str, negative: str) -> str:
    if value is None:
        return "na"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "na"
    direction = positive if numeric >= 0 else negative
    scaled = int(round(abs(numeric) * 10000))
    return f"{direction}{scaled:07d}"


def location_token() -> str:
    config = read_kv_config(str(LOCATION_CONF))
    lat = format_coord(config.get("LATITUDE"), "N", "S")
    lon = format_coord(config.get("LONGITUDE"), "E", "W")
    return f"{lat}_{lon}"


def source_start_datetime(source_path: Path) -> datetime | None:
    match = re.search(r"(\d{8}T\d{6})", source_path.stem)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def filename_time_token(source_path: Path, run: LabelRun, sample_rate: int) -> str:
    start_dt = source_start_datetime(source_path)
    if start_dt is None:
        return source_path.stem
    run_dt = start_dt + timedelta(seconds=(run.start_frame / sample_rate))
    return run_dt.strftime("%Y%m%dT%H%M%SZ")


def to_mono(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 1:
        return audio.astype(np.float32)
    return audio.astype(np.float32).mean(axis=1)


def collect_detections(audio_mono: np.ndarray, frames: int, model: BirdNETModel) -> List[Detection]:
    detections: List[Detection] = []
    if frames < WINDOW_FRAMES:
        padded = np.zeros(WINDOW_FRAMES, dtype=np.float32)
        padded[:frames] = audio_mono[:frames]
        label, score = invoke_birdnet_top_label(scale_by_max_value(padded), model)
        return [Detection(0, 0, frames, label, score)]

    window_index = 0
    for start in range(0, frames - WINDOW_FRAMES + 1, STRIDE_FRAMES):
        end = start + WINDOW_FRAMES
        label, score = invoke_birdnet_top_label(
            scale_by_max_value(audio_mono[start:end]), model
        )
        detections.append(Detection(window_index, start, end, label, score))
        window_index += 1
    return detections


def build_runs(detections: List[Detection]) -> List[LabelRun]:
    if not detections:
        return []

    runs: List[LabelRun] = []
    current = LabelRun(
        run_index=0,
        start_frame=detections[0].start_frame,
        end_frame=detections[0].end_frame,
        label=detections[0].label,
        peak_score=detections[0].score,
    )

    for detection in detections[1:]:
        same_label = detection.label == current.label
        overlaps = detection.start_frame <= current.end_frame
        if same_label and overlaps:
            current.end_frame = max(current.end_frame, detection.end_frame)
            current.peak_score = max(current.peak_score, detection.score)
            continue

        runs.append(current)
        current = LabelRun(
            run_index=len(runs),
            start_frame=detection.start_frame,
            end_frame=detection.end_frame,
            label=detection.label,
            peak_score=detection.score,
        )

    runs.append(current)
    return runs


def write_flac_runs(source_path: Path, audio: np.ndarray, sample_rate: int, runs: List[LabelRun]) -> List[tuple[LabelRun, Path]]:
    written = []
    loc_token = location_token()
    for run in runs:
        out_dir = label_output_dir(source_path, run.label)
        create_dir(out_dir, "sensos-admin", "sensos-data", 0o2775)
        start_sec = run.start_frame / sample_rate
        end_sec = run.end_frame / sample_rate
        filename = (
            f"{filename_time_token(source_path, run, sample_rate)}_"
            f"{loc_token}_"
            f"{run.run_index:03d}_"
            f"{sanitize_label(run.label)}_"
            f"{start_sec:09.3f}-{end_sec:09.3f}.flac"
        )
        flac_path = out_dir / filename
        chunk = audio[run.start_frame : run.end_frame]
        sf.write(flac_path, chunk, sample_rate, format="FLAC")
        written.append((run, flac_path))
    return written


def record_failure(conn: sqlite3.Connection, source_key: str, info: sf.SoundFile | None, error: str) -> None:
    sample_rate = getattr(info, "samplerate", None)
    channels = getattr(info, "channels", None)
    frames = getattr(info, "frames", None)
    conn.execute(
        """
        INSERT INTO processed_files (
            source_path, sample_rate, channels, frames, started_at, processed_at, status, error, deleted_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(source_path) DO UPDATE SET
            sample_rate=excluded.sample_rate,
            channels=excluded.channels,
            frames=excluded.frames,
            started_at=excluded.started_at,
            processed_at=excluded.processed_at,
            status=excluded.status,
            error=excluded.error,
            deleted_source=0
        """,
        (source_key, sample_rate, channels, frames, now_iso(), now_iso(), "error", error),
    )
    conn.commit()


def delete_bad_source(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        print(f"🗑️ Deleted unreadable source file {path}")
    except Exception as unlink_error:
        print(f"⚠️ Failed to delete unreadable source file {path}: {unlink_error}", file=sys.stderr)


def process_wav(model: BirdNETModel, conn: sqlite3.Connection, source_path: Path) -> None:
    source_key = relative_source(source_path)
    info = sf.info(source_path)
    if info.samplerate != SAMPLE_RATE:
        raise ValueError(
            f"Unsupported sample rate {info.samplerate} for {source_key}; expected {SAMPLE_RATE}"
        )

    conn.execute(
        """
        INSERT INTO processed_files (
            source_path, sample_rate, channels, frames, started_at, status, error, output_dir, deleted_source
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0)
        ON CONFLICT(source_path) DO UPDATE SET
            sample_rate=excluded.sample_rate,
            channels=excluded.channels,
            frames=excluded.frames,
            started_at=excluded.started_at,
            processed_at=NULL,
            status=excluded.status,
            error=NULL,
            output_dir=NULL,
            deleted_source=0
        """,
        (source_key, info.samplerate, info.channels, info.frames, now_iso(), "processing"),
    )
    conn.execute("DELETE FROM detections WHERE source_path = ?", (source_key,))
    conn.execute("DELETE FROM flac_runs WHERE source_path = ?", (source_key,))
    conn.commit()

    audio, sample_rate = sf.read(source_path, dtype="int32", always_2d=True)
    mono = to_mono(audio)
    detections = collect_detections(mono, len(mono), model)
    runs = build_runs(detections)
    written_runs = write_flac_runs(source_path, audio, sample_rate, runs)

    conn.executemany(
        """
        INSERT INTO detections (
            source_path, window_index, start_frame, end_frame, start_sec, end_sec, top_label, top_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                source_key,
                d.window_index,
                d.start_frame,
                d.end_frame,
                d.start_frame / sample_rate,
                d.end_frame / sample_rate,
                d.label,
                d.score,
            )
            for d in detections
        ],
    )
    conn.executemany(
        """
        INSERT INTO flac_runs (
            source_path, run_index, label, start_frame, end_frame, start_sec, end_sec, peak_score, flac_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                source_key,
                run.run_index,
                run.label,
                run.start_frame,
                run.end_frame,
                run.start_frame / sample_rate,
                run.end_frame / sample_rate,
                run.peak_score,
                flac_path.relative_to(INPUT_ROOT.parent).as_posix(),
            )
            for run, flac_path in written_runs
        ],
    )
    conn.execute(
        """
        UPDATE processed_files
        SET processed_at = ?, status = ?, error = NULL, output_dir = ?
        WHERE source_path = ?
        """,
        (
            now_iso(),
            "done",
            source_path.relative_to(INPUT_ROOT).parent.as_posix(),
            source_key,
        ),
    )
    conn.commit()

    source_path.unlink()
    conn.execute(
        "UPDATE processed_files SET deleted_source = 1 WHERE source_path = ?",
        (source_key,),
    )
    conn.commit()


def main() -> None:
    setup_logging("process_birdnet.log")
    ensure_runtime_dirs()
    conn = connect_db()
    model = None

    while True:
        next_wav = None
        try:
            if not MODEL_PATH.exists() or not LABELS_PATH.exists():
                print(
                    f"⚠️ BirdNET model files missing under {MODEL_ROOT}. Sleeping..."
                )
                time.sleep(IDLE_SLEEP_SEC)
                continue

            if model is None:
                print(f"🧠 Loading BirdNET model from {MODEL_PATH}")
                model = load_birdnet_model(MODEL_PATH, LABELS_PATH)

            next_wav = find_next_wav()
            if next_wav is None:
                time.sleep(IDLE_SLEEP_SEC)
                continue

            if not is_file_stable(next_wav):
                print(f"⏳ Skipping active or recently changed file {next_wav}")
                time.sleep(IDLE_SLEEP_SEC)
                continue

            print(f"🎧 Processing {next_wav}")
            process_wav(model, conn, next_wav)
            print(f"✅ Finished {next_wav}")
        except Exception as e:
            if next_wav is not None and next_wav.exists():
                try:
                    record_failure(conn, relative_source(next_wav), sf.info(next_wav), str(e))
                except Exception:
                    pass
                delete_bad_source(next_wav)
            print(f"❌ BirdNET processing failure: {e}", file=sys.stderr)
            time.sleep(ERROR_SLEEP_SEC)


if __name__ == "__main__":
    main()
