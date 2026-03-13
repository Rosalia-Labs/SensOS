#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

import os
import random
import shutil
import sys
import time
from pathlib import Path

sys.path.append("/sensos/lib")
from utils import setup_logging  # noqa: E402

DATA_ROOT = Path("/sensos/data")
AUDIO_ROOT = DATA_ROOT / "audio_recordings"
OUTPUT_ROOT = AUDIO_ROOT / "processed"
MIN_FREE_MB = int(os.environ.get("BIRDNET_MIN_FREE_MB", "100"))
IDLE_SLEEP_SEC = int(os.environ.get("BIRDNET_THIN_IDLE_SLEEP_SEC", "60"))
ERROR_SLEEP_SEC = int(os.environ.get("BIRDNET_THIN_ERROR_SLEEP_SEC", "30"))


def free_mb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 * 1024)


def label_dirs() -> list[Path]:
    if not OUTPUT_ROOT.exists():
        return []
    dirs = []
    for path in OUTPUT_ROOT.rglob("*"):
        if not path.is_dir():
            continue
        count = sum(1 for child in path.iterdir() if child.is_file() and child.suffix.lower() == ".flac")
        if count > 0:
            dirs.append(path)
    return dirs


def choose_victim_dir() -> Path | None:
    dirs = label_dirs()
    if not dirs:
        return None
    counts = {path: sum(1 for child in path.iterdir() if child.is_file() and child.suffix.lower() == ".flac") for path in dirs}
    max_count = max(counts.values())
    candidates = [path for path, count in counts.items() if count == max_count]
    return random.choice(candidates)


def choose_victim_file(directory: Path) -> Path | None:
    files = [child for child in directory.iterdir() if child.is_file() and child.suffix.lower() == ".flac"]
    return random.choice(files) if files else None


def prune_empty_dirs(start: Path) -> None:
    current = start
    while current != OUTPUT_ROOT and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def thin_once() -> bool:
    victim_dir = choose_victim_dir()
    if victim_dir is None:
        return False

    victim_file = choose_victim_file(victim_dir)
    if victim_file is None:
        return False

    print(f"🪶 Thinning {victim_file}")
    victim_file.unlink(missing_ok=True)
    prune_empty_dirs(victim_dir)
    return True


def main() -> None:
    setup_logging("thin_birdnet_flac.log")

    while True:
        try:
            current_free_mb = free_mb(DATA_ROOT)
            if current_free_mb >= MIN_FREE_MB:
                time.sleep(IDLE_SLEEP_SEC)
                continue

            print(
                f"⚠️ Free space low: {current_free_mb:.1f} MB < {MIN_FREE_MB} MB. Starting thinning."
            )
            while current_free_mb < MIN_FREE_MB:
                if not thin_once():
                    print("⚠️ No FLAC files available to thin.", file=sys.stderr)
                    break
                current_free_mb = free_mb(DATA_ROOT)
            print(f"✅ Thinning pass complete. Free space now {current_free_mb:.1f} MB")
            time.sleep(IDLE_SLEEP_SEC)
        except Exception as e:
            print(f"❌ Thinning failure: {e}", file=sys.stderr)
            time.sleep(ERROR_SLEEP_SEC)


if __name__ == "__main__":
    main()
