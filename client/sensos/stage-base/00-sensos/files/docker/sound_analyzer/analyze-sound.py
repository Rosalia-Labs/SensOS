import os
import time
import json
import numpy as np
import psycopg
import librosa
import sys
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("sound-analyzer")

print("🎬 Script starting...", flush=True)
logger.info("Script starting...")

MOCK_DATA = os.getenv("MOCK_DATA", "0") == "1"
if MOCK_DATA:
    logger.warning(
        "MOCK_DATA is enabled — generating random segments instead of querying the database."
    )

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
    logger.debug("Ensuring sound_statistics table exists...")
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
    logger.info("Sound statistics table created or already exists.")


def compute_audio_features(audio_segment):
    logger.debug(f"Computing audio features on segment of size {audio_segment.size}")
    if audio_segment.size == 0:
        return 0.0, 0.0, 0.0

    peak_amplitude = np.max(np.abs(audio_segment))
    rms = np.sqrt(np.mean(audio_segment.astype(np.float32) ** 2))
    snr = 0.0
    if rms > 1e-12:
        snr = 20 * np.log10(peak_amplitude / rms)
    logger.debug(f"Computed peak_amplitude={peak_amplitude}, rms={rms}, snr={snr}")
    return float(peak_amplitude), float(rms), float(snr)


def get_frequency_bins(min_freq, max_freq, num_bins):
    logger.debug(
        f"Creating frequency bins: {min_freq}Hz - {max_freq}Hz into {num_bins} bins"
    )
    return np.logspace(np.log10(min_freq), np.log10(max_freq), num_bins + 1)


def compute_binned_spectrum(audio_segment, min_freq=None, max_freq=None, num_bins=10):
    logger.debug("Computing binned spectrum...")
    if audio_segment.dtype != np.float32:
        logger.debug(f"Converting dtype {audio_segment.dtype} to float32.")
    audio_segment = audio_segment.astype(np.float32)

    if np.any(np.isnan(audio_segment)) or np.any(np.isinf(audio_segment)):
        logger.warning("NaNs or Infs detected in audio segment before STFT.")

    S = np.abs(librosa.stft(audio_segment, n_fft=N_FFT, hop_length=HOP_LENGTH)) ** 2
    freqs = librosa.fft_frequencies(sr=SAMPLE_RATE, n_fft=N_FFT)

    if min_freq is None or min_freq <= 0:
        min_freq = 50
    if max_freq is None or max_freq <= 0:
        max_freq = SAMPLE_RATE // 2

    bins = get_frequency_bins(min_freq, max_freq, num_bins)
    power = []
    for i in range(num_bins):
        mask = (freqs >= bins[i]) & (freqs < bins[i + 1])
        power_val = np.sum(S[mask, :])
        power.append(power_val)

    db = librosa.power_to_db(np.array(power), ref=1.0)
    logger.debug(f"Generated spectrum with {len(db)} bins.")
    return db.tolist()


def process_audio_segment(audio_bytes, audio_format):
    logger.debug(
        f"Processing segment with format {audio_format}, byte length {len(audio_bytes)}"
    )
    try:
        if audio_format in ["FLOAT_LE", "FLOAT_BE"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.float32)
        elif audio_format in ["FLOAT64_LE", "FLOAT64_BE"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.float64).astype(np.float32)
        elif audio_format in ["S8"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.int8).astype(np.float32)
        elif audio_format in ["U8"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.uint8).astype(np.float32)
        elif audio_format in ["S16_LE", "S16_BE"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        elif audio_format in ["U16_LE", "U16_BE"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.uint16).astype(np.float32)
        elif audio_format in ["S24_LE", "S24_BE", "S24_3LE", "S24_3BE"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.int32).astype(np.float32)
        elif audio_format in ["U24_LE", "U24_BE", "U24_3LE", "U24_3BE"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.uint32).astype(np.float32)
        elif audio_format in ["S32_LE", "S32_BE"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.int32).astype(np.float32)
        elif audio_format in ["U32_LE", "U32_BE"]:
            audio_np = np.frombuffer(audio_bytes, dtype=np.uint32).astype(np.float32)
        else:
            logger.error(f"Unsupported format: {audio_format}")
            sys.exit(1)

        if len(audio_np) != SEGMENT_SIZE:
            logger.error(
                f"Segment length mismatch: expected {SEGMENT_SIZE}, got {len(audio_np)}"
            )
            sys.exit(1)

        audio_np = audio_np.astype(np.float32)

        if np.any(np.isnan(audio_np)) or np.any(np.isinf(audio_np)):
            logger.warning("NaNs or Infs detected after conversion.")

        return audio_np
    except Exception as e:
        logger.exception(f"Error in process_audio_segment: {e}")
        raise


def get_unprocessed_segments(conn):
    if MOCK_DATA:
        logger.info("MOCK_DATA enabled: generating 3 fake segments.")
        return [(i, None, None) for i in range(1, 4)]

    logger.debug("Querying unprocessed segments from database...")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ra.segment_id, ra.data, af.native_format
            FROM sensos.raw_audio ra
            JOIN sensos.audio_segments r ON ra.segment_id = r.id
            JOIN sensos.audio_files af ON r.file_id = af.id
            LEFT JOIN sensos.sound_statistics ss ON ra.segment_id = ss.segment_id
            WHERE ss.segment_id IS NULL;
            """
        )
        results = cur.fetchall()
    logger.info(f"Retrieved {len(results)} unprocessed segments.")
    return results


def store_sound_statistics(
    conn, segment_id, peak_amplitude, rms, snr, full_spectrum, bioacoustic_spectrum
):
    logger.debug(f"Storing statistics for segment {segment_id}")
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
    logger.info(f"Committed statistics for segment {segment_id}.")


def wait_for_schema(retries=30, delay=5):
    if MOCK_DATA:
        logger.info("MOCK_DATA enabled: skipping schema check.")
        return

    logger.info("Waiting for database schema to be ready...")
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
                        logger.info("Schema check passed.")
                        return
                    else:
                        print(
                            f"⏳ Schema or tables not yet ready (attempt {attempt+1}/{retries})."
                        )
                        logger.debug(f"Schema check attempt {attempt+1} failed.")
        except Exception as e:
            print(f"⚠️ Database connection issue (attempt {attempt+1}/{retries}): {e}")
            logger.warning(f"Connection issue on attempt {attempt+1}: {e}")
        time.sleep(delay)
    logger.error("Schema check failed after max retries.")
    raise RuntimeError("❌ Schema and tables not found after maximum retries.")


def main():
    print("🔄 Waiting for schema and tables to be ready...")
    wait_for_schema()

    conn = psycopg.connect(**DB_PARAMS) if not MOCK_DATA else None
    if conn:
        print("✅ Connected to the database for sound analysis.")
        logger.info("Connected to database.")
        create_sound_statistics_table(conn)

    while True:
        print("🔎 Checking for new raw audio segments to analyze...")
        segments = (
            get_unprocessed_segments(conn) if conn else get_unprocessed_segments(None)
        )

        if not segments:
            print("😴 No new segments found. Sleeping for 5 seconds...")
            time.sleep(5)
            continue

        for segment_id, audio_bytes, stored_dtype in segments:
            logger.info(f"Processing segment {segment_id} ({stored_dtype})")
            if MOCK_DATA:
                audio_np = np.random.uniform(-1, 1, size=SEGMENT_SIZE).astype(
                    np.float32
                )
            else:
                audio_np = process_audio_segment(audio_bytes, stored_dtype)

            peak_amplitude, rms, snr = compute_audio_features(audio_np)
            full_spectrum = compute_binned_spectrum(
                audio_np, num_bins=FULL_SPECTRUM_BINS
            )
            bioacoustic_spectrum = compute_binned_spectrum(
                audio_np, min_freq=1000, max_freq=8000, num_bins=BIOACOUSTIC_BINS
            )

            if conn:
                store_sound_statistics(
                    conn,
                    segment_id,
                    peak_amplitude,
                    rms,
                    snr,
                    full_spectrum,
                    bioacoustic_spectrum,
                )
            else:
                logger.info(
                    f"[MOCK] Segment {segment_id}: peak={peak_amplitude:.3f}, rms={rms:.3f}, snr={snr:.2f}"
                )

        print("✅ Batch complete. Sleeping for 5 seconds...")
        logger.info("Batch complete. Sleeping...")
        time.sleep(5)


if __name__ == "__main__":
    main()
