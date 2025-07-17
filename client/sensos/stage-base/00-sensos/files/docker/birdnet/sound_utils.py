import librosa
import numpy as np
import tflite_runtime.interpreter as tflite

from typing import Tuple, Dict, Optional
from dataclasses import dataclass

import datetime


@dataclass
class BirdNETModel:
    """
    A container for the BirdNET TFLite model and metadata required for inference.
    """

    interpreter: tflite.Interpreter
    input_details: list
    output_details: list
    labels: list[str]


def load_birdnet_model(model_path: str, labels_path: str) -> BirdNETModel:
    """
    Loads the BirdNET TFLite model and associated label file.

    Args:
        model_path: Path to the .tflite model file.
        labels_path: Path to the label file.

    Returns:
        A BirdNETModel dataclass instance.
    """
    interpreter = tflite.Interpreter(model_path=model_path)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
    with open(labels_path, "r") as f:
        labels = [
            f"{common} ({sci})" if "_" in line else line.strip()
            for line in f.readlines()
            for sci, common in [line.strip().split("_", 1)]
        ]
    return BirdNETModel(interpreter, input_details, output_details, labels)


def invoke_birdnet(
    audio: np.ndarray,
    model: BirdNETModel,
) -> Tuple[np.ndarray, Dict[str, float], float, float]:
    """
    Runs BirdNET inference on a mono audio segment.

    Args:
        audio: A 1D NumPy array of float32 audio samples in range [-1, 1].
        model: The BirdNETModel containing interpreter and metadata.

    Returns:
        - The 1024-dimensional embedding vector.
        - A dict of top-5 class label scores.
        - The Hill number (diversity index).
        - The Simpson index.
    """
    input_data = np.expand_dims(audio, axis=0).astype(np.float32)
    model.interpreter.set_tensor(model.input_details[0]["index"], input_data)
    model.interpreter.invoke()
    scores = model.interpreter.get_tensor(model.output_details[0]["index"])
    embedding = model.interpreter.get_tensor(model.output_details[0]["index"] - 1)
    scores_flat = flat_sigmoid(scores.flatten())
    embedding_flat = embedding.flatten()
    total = np.sum(scores_flat)
    probs = scores_flat / total if total > 0 else np.zeros_like(scores_flat)
    entropy = -np.sum(probs[probs > 0] * np.log2(probs[probs > 0]))
    return (
        embedding_flat,
        {model.labels[i]: scores_flat[i] for i in np.argsort(scores_flat)[-5:][::-1]},
        float(2**entropy),
        float(np.sum(probs**2)),
    )


def flat_sigmoid(
    x: np.ndarray, sensitivity: float = -1, bias: float = 1.0
) -> np.ndarray:
    """
    Applies a sigmoid transformation with adjustable sensitivity and bias.

    Args:
        x: Input array.
        sensitivity: Multiplier applied before sigmoid.
        bias: Horizontal shift of the sigmoid curve.

    Returns:
        Transformed array with values in (0, 1).
    """
    return 1 / (1.0 + np.exp(sensitivity * np.clip((x + (bias - 1.0) * 10.0), -20, 20)))


def compute_audio_features(audio: np.ndarray) -> Tuple[float, float, float]:
    """
    Computes peak amplitude, RMS, and signal-to-noise ratio (SNR).

    Args:
        audio: Audio signal as NumPy array.

    Returns:
        Tuple of (peak amplitude, RMS, SNR in dB).
    """
    audio = audio.astype(np.float64)
    flat_audio = audio.flatten()
    peak = float(np.max(np.abs(flat_audio)))
    rms = float(np.sqrt(np.mean(flat_audio**2)))
    snr = float(20 * np.log10(peak / rms)) if rms > 1e-12 else 0.0
    return peak, rms, snr


def get_freq_bins(min_f: float, max_f: float, bins: int) -> np.ndarray:
    """
    Computes logarithmically spaced frequency bin edges.

    Args:
        min_f: Minimum frequency (Hz).
        max_f: Maximum frequency (Hz).
        bins: Number of bins.

    Returns:
        NumPy array of bin edges.
    """
    return np.logspace(np.log10(min_f), np.log10(max_f), bins + 1)


def compute_binned_spectrum(
    audio: np.ndarray,
    sample_rate: int,
    n_fft: int,
    hop_length: int,
    min_freq: float,
    max_freq: float,
    bins: int,
) -> list[float]:
    """
    Computes a binned log-power spectrogram over the given frequency range.

    Args:
        audio: 1D array of audio samples.
        sample_rate: Audio sampling rate.
        n_fft: FFT window size.
        hop_length: Step size between FFTs.
        min_freq: Minimum frequency to consider.
        max_freq: Maximum frequency to consider.
        bins: Number of output frequency bins.

    Returns:
        List of log-scaled power values (in dB) for each bin.
    """
    S = np.abs(librosa.stft(audio, n_fft=n_fft, hop_length=hop_length)) ** 2
    freqs = librosa.fft_frequencies(sr=sample_rate, n_fft=n_fft)
    bin_edges = get_freq_bins(min_freq, max_freq, bins)
    return librosa.power_to_db(
        [
            np.sum(S[(freqs >= bin_edges[i]) & (freqs < bin_edges[i + 1])])
            for i in range(bins)
        ],
        ref=1.0,
    ).tolist()


def scale_by_max_value(audio: np.ndarray) -> np.ndarray:
    """
    Normalizes the audio signal to [-1.0, 1.0], using a scale factor
    compatible with libsoundfile's 16-bit PCM scaling convention.

    Args:
        audio: 1D array of raw audio samples (e.g., int32).

    Returns:
        Normalized float32 array.
    """
    max_val = np.max(np.abs(audio))
    if max_val == 0:
        return np.zeros_like(audio, dtype=np.float32)
    scale = max_val * (32768.0 / 32767.0)
    return (audio / scale).astype(np.float32)


def invoke_birdnet_with_location(
    audio: np.ndarray,
    model: BirdNETModel,
    meta_model: BirdNETModel,
    latitude: float,
    longitude: float,
    date: datetime.date,
) -> Tuple[np.ndarray, Dict[str, Tuple[float, Optional[float]]], float, float]:
    """
    Like invoke_birdnet, but also returns the locality likelihood ("likely" score) for each top label.
    If latitude and longitude are both zero, the likely score is None.

    Returns:
        - Embedding vector
        - Dict: label -> (audio_score, likely_score or None)
        - Hill number
        - Simpson index
    """
    # --- Standard BirdNET audio inference ---
    input_data = np.expand_dims(audio, axis=0).astype(np.float32)
    model.interpreter.set_tensor(model.input_details[0]["index"], input_data)
    model.interpreter.invoke()
    scores = model.interpreter.get_tensor(model.output_details[0]["index"])
    embedding = model.interpreter.get_tensor(model.output_details[0]["index"] - 1)
    scores_flat = flat_sigmoid(scores.flatten())
    embedding_flat = embedding.flatten()
    total = np.sum(scores_flat)
    probs = scores_flat / total if total > 0 else np.zeros_like(scores_flat)
    entropy = -np.sum(probs[probs > 0] * np.log2(probs[probs > 0]))

    # --- Run meta-model for locality scores (unless lat/lon both zero) ---
    likely_scores = None
    if not (latitude == 0 and longitude == 0):
        week = date.isocalendar()[1]
        week = min(max(week, 1), 48)
        sample = np.expand_dims(
            np.array([latitude, longitude, week], dtype="float32"), 0
        )
        meta_model.interpreter.set_tensor(meta_model.input_details[0]["index"], sample)
        meta_model.interpreter.invoke()
        likely_scores = meta_model.interpreter.get_tensor(
            meta_model.output_details[0]["index"]
        )[0]

    # --- Build combined top-5 dictionary ---
    top_indices = np.argsort(scores_flat)[-5:][::-1]
    top_scores = {}
    for i in top_indices:
        likely = float(likely_scores[i]) if likely_scores is not None else None
        top_scores[model.labels[i]] = (float(scores_flat[i]), likely)

    return (
        embedding_flat,
        top_scores,
        float(2**entropy),
        float(np.sum(probs**2)),
    )
