import numpy as np
import soundfile as sf
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger("storage-utils")


def overwrite_segment_with_zeros(seg: Dict[str, Any], audio_base: Path) -> bool:
    """Overwrite the segment with zeros in-place in the WAV file."""
    full_path = audio_base / seg["file_path"]
    frame_count = seg["end_frame"] - seg["start_frame"]
    if not full_path.exists():
        logger.warning(f"Missing file on disk: {full_path}")
        return False

    try:
        with sf.SoundFile(full_path, mode="r+") as f:
            f.seek(seg["start_frame"])
            zeros = np.zeros((frame_count, f.channels), dtype="float32")
            f.write(zeros)
            f.flush()
        logger.info(
            f'Wrote zeros to {seg["file_path"]} at frame {seg["start_frame"]} for {frame_count} frames'
        )
        return True
    except Exception as e:
        logger.error(f'Failed to zero segment in {seg["file_path"]}: {e}')
        return False


def delete_audio_file_from_disk(seg: Dict[str, Any], audio_base: Path) -> None:
    """Delete an audio file from disk."""
    file_path = audio_base / seg["file_path"]
    if file_path.exists():
        try:
            file_path.unlink()
            logger.info(f"Deleted fully zeroed file: {file_path}")
        except Exception as e:
            logger.error(f"Failed to delete zeroed file {file_path}: {e}")


def compress_file_to_flac(wav_path: Path) -> Optional[Path]:
    """
    Compress a .wav to .flac, delete .wav, return .flac path or None on failure.
    """
    if not wav_path.exists() or wav_path.suffix.lower() != ".wav":
        return None
    flac_path = wav_path.with_suffix(".flac")
    try:
        data, sr = sf.read(wav_path)
        sf.write(flac_path, data, sr, format="FLAC")
        wav_path.unlink()
        logger.info(f"Compressed {wav_path} to {flac_path}")
        return flac_path
    except Exception as e:
        logger.error(f"Failed to compress {wav_path} to FLAC: {e}")
        return None


def get_disk_free_gb_and_percent(path: Path) -> Optional[Dict[str, float]]:
    """
    Get disk space stats for a given Path.
    Returns dict with keys: disk_available_gb, percent_free, total_gb.
    """
    import shutil

    try:
        total, used, free = shutil.disk_usage(str(path))
        free_gb = free / (1024**3)
        percent_free = 100 * free / total if total else 0
        return {
            "disk_available_gb": round(free_gb, 2),
            "percent_free": round(percent_free, 2),
            "total_gb": round(total / (1024**3), 2),
        }
    except Exception as e:
        logger.warning(f"Could not get disk usage for {path}: {e}")
        return None


def safe_filename(s: str) -> str:
    """Return a safe filename (no spaces/special chars, keeps alphanum, dash, underscore, dot)."""
    import re

    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)
