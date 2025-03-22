import os
import time
import json
import numpy as np
import psycopg
import librosa

# Database connection details from environment variables.
DB_PARAMS = {
    "dbname": os.getenv("POSTGRES_DB", "postgres"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "sensos"),
    "host": os.getenv("DB_HOST", "sensos-client-database"),
    "port": os.getenv("DB_PORT", "5432"),
}

# Audio settings (must match how raw audio was stored)
SAMPLE_RATE = 48000  # Hz (BirdNET recommended)
SEGMENT_DURATION = 3  # seconds
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION  # Number of samples per segment

# STFT and spectrum parameters
N_FFT = 2048
HOP_LENGTH = 512

# Number of bins for the spectra
FULL_SPECTRUM_BINS = 20
BIOACOUSTIC_BINS = 20


def create_sound_statistics_table(conn):
    """Creates a table for storing computed sound statistics if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sensos.sound_statistics (
                segment_id INTEGER PRIMARY KEY REFERENCES sensos.raw_audio(segment_id) ON DELETE CASCADE,
                peak_amplitude FLOAT,
                rms FLOAT,
                snr FLOAT,
                full_spectrum JSONB,
                bioacoustic_spectrum JSONB
            );
        """
        )
        conn.commit()
    print("Sound statistics table is ready.")


def compute_audio_features(audio_segment):
    """Compute basic audio features: peak amplitude, RMS, and SNR."""
    if audio_segment.size == 0:
        return 0.0, 0.0, 0.0

    peak_amplitude = np.max(np.abs(audio_segment))
    rms = np.sqrt(np.mean(audio_segment.astype(np.float32) ** 2))
    snr = 0.0
    if rms > 1e-12:
        snr = 20 * np.log10(peak_amplitude / rms)
    return float(peak_amplitude), float(rms), float(snr)


def get_frequency_bins(min_freq, max_freq, num_bins):
    """Generate logarithmically spaced frequency bins."""
    return np.logspace(np.log10(min_freq), np.log10(max_freq), num_bins + 1)


def compute_binned_spectrum(audio_segment, min_freq=None, max_freq=None, num_bins=10):
    """
    Compute the integrated power within logarithmic frequency bins for the given audio segment.
    Returns the spectrum in decibels.
    """
    # Compute power spectrogram.
    S = (
        np.abs(
            librosa.stft(
                audio_segment.astype(float), n_fft=N_FFT, hop_length=HOP_LENGTH
            )
        )
        ** 2
    )
    freqs = librosa.fft_frequencies(sr=SAMPLE_RATE, n_fft=N_FFT)

    if min_freq is None or min_freq <= 0:
        min_freq = 50  # Default lower bound
    if max_freq is None or max_freq <= 0:
        max_freq = SAMPLE_RATE // 2  # Nyquist frequency

    bins = get_frequency_bins(min_freq, max_freq, num_bins)
    power = []
    for i in range(num_bins):
        mask = (freqs >= bins[i]) & (freqs < bins[i + 1])
        power_val = np.sum(S[mask, :])
        power.append(power_val)

    # Convert power to decibels.
    db = librosa.power_to_db(np.array(power), ref=1.0)
    return db.tolist()


def process_audio_segment(audio_bytes, stored_dtype):
    """
    Convert raw audio bytes into a numpy array normalized to float32 in [-1, 1].
    The 'stored_dtype' parameter is a string indicating the NumPy data type of the stored bytes.
    Returns None if the segment size is incorrect.
    """
    if stored_dtype == "float32":
        audio_np = np.frombuffer(audio_bytes, dtype=np.float32)
    elif stored_dtype == "int16":
        # Convert from int16 to float32 and normalize.
        audio_np = (
            np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )
    else:
        print(f"Unsupported stored data type: {stored_dtype}. Skipping segment.")
        return None

    if len(audio_np) != SEGMENT_SIZE:
        print(
            f"Segment length mismatch: expected {SEGMENT_SIZE}, got {len(audio_np)}. Skipping."
        )
        return None
    return audio_np


def get_unprocessed_segments(conn):
    """
    Retrieve raw audio segments that have not yet been analyzed,
    along with the stored data type from the recording session.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ra.segment_id, ra.data, rs.raw_audio_dtype
            FROM sensos.raw_audio ra
            JOIN sensos.audio_segments r ON ra.segment_id = r.id
            JOIN sensos.recording_sessions rs ON r.session_id = rs.id
            LEFT JOIN sensos.sound_statistics ss ON ra.segment_id = ss.segment_id
            WHERE ss.segment_id IS NULL;
        """
        )
        results = cur.fetchall()
    return results


def store_sound_statistics(
    conn, segment_id, peak_amplitude, rms, snr, full_spectrum, bioacoustic_spectrum
):
    """Store computed sound statistics in the database."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sensos.sound_statistics 
                (segment_id, peak_amplitude, rms, snr, full_spectrum, bioacoustic_spectrum)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (segment_id) DO NOTHING;
        """,
            (
                segment_id,
                peak_amplitude,
                rms,
                snr,
                json.dumps(full_spectrum),
                json.dumps(bioacoustic_spectrum),
            ),
        )
        conn.commit()
    print(f"Stored sound statistics for segment {segment_id}.")


def wait_for_schema(retries=30, delay=5):
    """Wait until the 'sensos' schema and required tables exist."""
    for attempt in range(retries):
        try:
            with psycopg.connect(**DB_PARAMS) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.tables 
                            WHERE table_schema = 'sensos' AND table_name = 'raw_audio'
                        );
                    """
                    )
                    exists = cur.fetchone()[0]
                    if exists:
                        print("✅ Required schema and tables detected.")
                        return
                    else:
                        print(
                            f"⏳ Schema or tables not yet ready (attempt {attempt+1}/{retries})."
                        )
        except Exception as e:
            print(f"⚠️ Database connection issue (attempt {attempt+1}/{retries}): {e}")
        time.sleep(delay)
    raise RuntimeError("❌ Schema and tables not found after maximum retries.")


def main():
    print("🔄 Waiting for schema and tables to be ready...")
    wait_for_schema()

    # Connect to the database.
    conn = psycopg.connect(**DB_PARAMS)
    print("✅ Connected to the database for sound analysis.")

    # Ensure the sound statistics table exists.
    create_sound_statistics_table(conn)

    while True:
        print("🔎 Checking for new raw audio segments to analyze...")
        segments = get_unprocessed_segments(conn)
        if not segments:
            print("😴 No new segments found. Sleeping for 5 seconds...")
            time.sleep(5)
            continue

        for segment_id, audio_bytes, stored_dtype in segments:
            print(
                f"🎧 Processing segment {segment_id} (stored type: {stored_dtype})..."
            )
            audio_np = process_audio_segment(audio_bytes, stored_dtype)
            if audio_np is None:
                continue

            # Compute audio features.
            peak_amplitude, rms, snr = compute_audio_features(audio_np)

            # Compute spectral features.
            full_spectrum = compute_binned_spectrum(
                audio_np, num_bins=FULL_SPECTRUM_BINS
            )
            bioacoustic_spectrum = compute_binned_spectrum(
                audio_np, min_freq=1000, max_freq=8000, num_bins=BIOACOUSTIC_BINS
            )

            # Store computed statistics.
            store_sound_statistics(
                conn,
                segment_id,
                peak_amplitude,
                rms,
                snr,
                full_spectrum,
                bioacoustic_spectrum,
            )

        print("✅ Batch complete. Sleeping for 5 seconds...")
        time.sleep(5)


if __name__ == "__main__":
    main()
