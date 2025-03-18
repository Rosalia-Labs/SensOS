import sounddevice as sd
import numpy as np
import signal
import sys
import psycopg
import librosa
import queue
import time
import json
import threading
import datetime
import os

# Get connection details from environment variables, with defaults for testing.
db_name = os.environ.get("POSTGRES_DB", "postgres")
db_user = os.environ.get("POSTGRES_USER", "postgres")
db_password = os.environ.get("POSTGRES_PASSWORD", "sensos")
db_host = os.environ.get("DB_HOST", "sensos-client-test-database")
db_port = os.environ.get("DB_PORT", "5432")
DB_PARAMS = f"dbname={db_name} user={db_user} password={db_password} host={db_host} port={db_port}"

# Audio settings
SAMPLE_RATE = 48000  # Hz (BirdNET recommended)
CHANNELS = 1  # Mono
BIT_DEPTH = 16  # Bit depth
SEGMENT_DURATION = 3  # seconds
STEP_SIZE = 1  # seconds (new segment every second)
SEGMENT_SIZE = SAMPLE_RATE * SEGMENT_DURATION  # Frames per segment
STEP_SIZE_FRAMES = SAMPLE_RATE * STEP_SIZE  # Frames per step
AUDIO_FORMAT = np.int16  # 16-bit PCM

# Determine the audio source: "record" for live capture or "random" for test signal.
AUDIO_SOURCE = os.environ.get("AUDIO_SOURCE", "record").lower()

# Get the recording duration (in seconds) from environment variables.
try:
    RECORD_DURATION = int(os.environ.get("RECORD_DURATION", "0"))
except ValueError:
    RECORD_DURATION = 0

# Define STFT parameters
N_FFT = 2048  # FFT window size
HOP_LENGTH = 512  # Overlap between frames
N_BINS = int(os.environ.get("N_BINS", "10"))  # Number of logarithmic frequency bins

# Define bin counts for full-spectrum and bioacoustic-spectrum
FULL_SPECTRUM_BINS = N_BINS  # Example: full range
BIOACOUSTIC_BINS = N_BINS  # Example: 1-8 kHz range

# Circular buffer for overlapping audio storage
buffer = np.zeros(SEGMENT_SIZE, dtype=AUDIO_FORMAT)

# Queue for audio segments
audio_queue = queue.Queue()


def get_frequency_bins(min_freq, max_freq, num_bins):
    """Generate logarithmic frequency bin boundaries."""
    return np.logspace(
        np.log2(min_freq), np.log2(max_freq), num_bins + 1, base=2
    ).tolist()


def connect_with_retry(max_attempts=10, delay=3):
    """Try to connect to the database, retrying if the connection is refused."""
    for attempt in range(max_attempts):
        try:
            conn = psycopg.connect(DB_PARAMS)
            print("Connected to database!")
            return conn
        except psycopg.OperationalError as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(delay)
    raise Exception("Failed to connect to database after multiple attempts")


# Establish the connection.
conn = connect_with_retry()
cursor = conn.cursor()


def initialize_schema():
    """Initializes the database schema for recording sessions and audio segments."""
    cursor.execute(
        """
        CREATE EXTENSION IF NOT EXISTS postgis;
        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE SCHEMA IF NOT EXISTS sensos;
        """
    )
    # Create table for recording sessions.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.recording_sessions (
            id SERIAL PRIMARY KEY,
            start_time TIMESTAMPTZ NOT NULL,
            sample_rate INT NOT NULL,
            bit_depth INT NOT NULL,
            channel_count INT NOT NULL,
            device TEXT,
            full_freq_bins JSONB,
            bio_freq_bins JSONB
        );
        """
    )
    # Create table for audio segments (metadata only).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.recordings (
            id SERIAL PRIMARY KEY,
            session_id INTEGER REFERENCES sensos.recording_sessions(id) ON DELETE CASCADE,
            timestamp TIMESTAMPTZ NOT NULL,
            end_timestamp TIMESTAMPTZ NOT NULL,
            peak_amplitude FLOAT,
            rms FLOAT,
            snr FLOAT
        );
        """
    )
    # Create table for raw audio storage.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.raw_audio (
            segment_id INTEGER PRIMARY KEY REFERENCES sensos.recordings(id) ON DELETE CASCADE,
            data BYTEA NOT NULL
        );
        """
    )
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS sensos.full_spectra (
            segment_id INTEGER PRIMARY KEY REFERENCES sensos.recordings(id) ON DELETE CASCADE,
            spectrum vector({FULL_SPECTRUM_BINS}) NOT NULL
        );
        """
    )
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS sensos.bioacoustic_spectra (
            segment_id INTEGER PRIMARY KEY REFERENCES sensos.recordings(id) ON DELETE CASCADE,
            spectrum vector({BIOACOUSTIC_BINS}) NOT NULL
        );
        """
    )
    conn.commit()
    print("Database schema initialized.")


# Initialize the schema at startup.
initialize_schema()


def start_new_session():
    """Creates a new recording session in the database and returns session ID."""

    full_bins = get_frequency_bins(50, SAMPLE_RATE // 2, FULL_SPECTRUM_BINS)
    bio_bins = get_frequency_bins(1000, 8000, BIOACOUSTIC_BINS)

    cursor.execute(
        """
        INSERT INTO sensos.recording_sessions (
            start_time,
            sample_rate,
            bit_depth,
            channel_count,
            device,
            full_freq_bins,
            bio_freq_bins
        )
        VALUES (
            NOW(),
            %s, %s, %s, %s,
            %s::jsonb,
            %s::jsonb
        )
        RETURNING id;
        """,
        (
            SAMPLE_RATE,
            BIT_DEPTH,
            CHANNELS,
            (
                "Raspberry Pi Built-in Mic"
                if AUDIO_SOURCE == "record"
                else "Test Random Signal"
            ),
            json.dumps(full_bins),
            json.dumps(bio_bins),
        ),
    )
    session_id = cursor.fetchone()[0]
    conn.commit()
    return session_id


def compute_binned_spectrum(audio_segment, min_freq=None, max_freq=None, num_bins=10):
    """Compute integrated sound energy across logarithmic frequency bins within a given range, then convert to decibels."""
    # Determine frequency range
    if min_freq is None or min_freq <= 0:
        min_freq = 50  # Default to 50 Hz
    if max_freq is None or max_freq <= 0:
        max_freq = SAMPLE_RATE // 2  # Nyquist frequency

    # Compute power spectrogram
    S = (
        np.abs(
            librosa.stft(
                audio_segment.astype(float), n_fft=N_FFT, hop_length=HOP_LENGTH
            )
        )
        ** 2
    )
    freqs = librosa.fft_frequencies(sr=SAMPLE_RATE, n_fft=N_FFT)

    # Define logarithmic bins within the selected frequency range
    bins = get_frequency_bins(min_freq, max_freq, num_bins + 1)
    power = np.zeros(num_bins, dtype=np.float32)

    for i in range(num_bins):
        mask = (freqs >= bins[i]) & (freqs < bins[i + 1])
        power[i] = np.sum(S[mask, :])  # Sum energy in bin

    db = librosa.power_to_db(power, ref=1.0)

    return db


def compute_audio_features(audio_segment):
    """Compute peak amplitude, RMS, and SNR safely."""
    if audio_segment.size == 0:
        print("Warning: Received an empty audio segment.")
        return 0.0, 0.0, 0.0

    if not np.all(np.isfinite(audio_segment)):
        print("Warning: audio_segment contains NaN or Inf values.")
        return 0.0, 0.0, 0.0

    peak_amplitude = np.max(np.abs(audio_segment))

    # Convert to float before squaring to prevent integer overflow
    rms = np.sqrt(np.mean(audio_segment.astype(np.float32) ** 2))

    snr = 0.0
    if rms > 1e-12:  # Avoid divide-by-zero
        snr = 20 * np.log10(peak_amplitude / rms)

    return peak_amplitude, rms, snr


def store_audio(segment, start_timestamp, end_timestamp, session_id):
    """Stores a 3-second audio segment with metadata, raw data, and both full and bioacoustic spectra."""
    peak_amplitude, rms, snr = compute_audio_features(segment)

    # Compute both full-spectrum and bioacoustic-spectrum vectors
    full_spectrum = compute_binned_spectrum(segment, num_bins=FULL_SPECTRUM_BINS)
    bioacoustic_spectrum = compute_binned_spectrum(
        segment, min_freq=1000, max_freq=8000, num_bins=BIOACOUSTIC_BINS
    )

    cursor.execute(
        """
        INSERT INTO sensos.recordings (session_id, timestamp, end_timestamp, peak_amplitude, rms, snr)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id;
        """,
        (session_id, start_timestamp, end_timestamp, peak_amplitude, rms, snr),
    )
    segment_id = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO sensos.raw_audio (segment_id, data)
        VALUES (%s, %s);
        """,
        (segment_id, psycopg.Binary(segment.tobytes())),
    )

    cursor.execute(
        """
        INSERT INTO sensos.full_spectra (segment_id, spectrum)
        VALUES (%s, %s);
        """,
        (segment_id, full_spectrum.tolist()),
    )

    cursor.execute(
        """
        INSERT INTO sensos.bioacoustic_spectra (segment_id, spectrum)
        VALUES (%s, %s);
        """,
        (segment_id, bioacoustic_spectrum.tolist()),
    )

    conn.commit()
    print(f"Stored segment {segment_id} from {start_timestamp} to {end_timestamp}.")


# Start a new session at script startup.
session_id = start_new_session()
print(f"Started recording session {session_id} using mode: {AUDIO_SOURCE}")

# Start the background thread.
db_thread = threading.Thread(
    target=lambda: [store_audio(*audio_queue.get(), session_id) for _ in iter(int, 1)],
    daemon=True,
)
db_thread.start()


def cleanup():
    """Gracefully shutdown the recording process."""
    print("\nShutting down... Flushing remaining audio data.")

    # Stop background thread gracefully
    audio_queue.put(None)
    db_thread.join()

    # Close database connections
    cursor.close()
    conn.close()
    print("Database connection closed. Exiting.")

    sys.exit(0)  # Ensure clean exit


def wait_for_container_stop():
    """Keep container running until stopped, while handling cleanup signals."""
    print("Recording complete. Waiting indefinitely for container shutdown.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()


def run_recording():
    """Handles live recording or test signal generation."""
    start_time = time.time()

    if AUDIO_SOURCE == "record":
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=AUDIO_FORMAT,
                callback=callback,
            ):
                print(
                    "Recording... Press Ctrl+C to stop (or wait for RECORD_DURATION)."
                )
                while True:
                    time.sleep(1)
                    if (
                        RECORD_DURATION > 0
                        and time.time() - start_time >= RECORD_DURATION
                    ):
                        print(
                            f"RECORD_DURATION {RECORD_DURATION} reached. Waiting indefinitely."
                        )
                        break
        except Exception as e:
            print(f"Audio capture failed: {e}")
            raise

    elif AUDIO_SOURCE == "random":
        print("Generating random test signal. Press Ctrl+C to stop.")
        try:
            while True:
                random_segment = np.random.randint(
                    -32768, 32767, SEGMENT_SIZE, dtype=AUDIO_FORMAT
                )
                start_timestamp = datetime.datetime.utcnow()
                end_timestamp = start_timestamp + datetime.timedelta(
                    seconds=SEGMENT_DURATION
                )
                audio_queue.put((random_segment, start_timestamp, end_timestamp))

                time.sleep(STEP_SIZE)

                if RECORD_DURATION > 0 and time.time() - start_time >= RECORD_DURATION:
                    print(
                        f"RECORD_DURATION {RECORD_DURATION} reached. Waiting indefinitely."
                    )
                    break
        except KeyboardInterrupt:
            print("\nStopping test signal generation...")

    elif AUDIO_SOURCE == "file":
        try:
            audio_data, sr = librosa.load(
                "test.wav", sr=SAMPLE_RATE, mono=True, dtype=np.float32
            )
            if sr != SAMPLE_RATE:
                print(f"Warning: Resampling from {sr} Hz to {SAMPLE_RATE} Hz")
                audio_data = librosa.resample(
                    audio_data, orig_sr=sr, target_sr=SAMPLE_RATE
                )

            # Convert float32 to 16-bit PCM
            audio_data = (audio_data * 32767).astype(AUDIO_FORMAT)

            # Read in 3-second segments
            start_idx = 0
            total_frames = len(audio_data)
            start_time = datetime.datetime.utcnow()

            while start_idx + SEGMENT_SIZE <= total_frames:
                segment = audio_data[start_idx : start_idx + SEGMENT_SIZE]
                end_time = start_time + datetime.timedelta(seconds=SEGMENT_DURATION)

                audio_queue.put((segment, start_time, end_time))

                start_idx += STEP_SIZE_FRAMES
                start_time = start_time + datetime.timedelta(seconds=STEP_SIZE)

                if RECORD_DURATION > 0 and (start_idx / SAMPLE_RATE) >= RECORD_DURATION:
                    print(
                        f"RECORD_DURATION {RECORD_DURATION} reached. Stopping file playback."
                    )
                    break

            print("Finished reading file. Waiting indefinitely.")
            wait_for_container_stop()

        except Exception as e:
            print(f"Error reading audio file: {e}")
            raise

    else:
        raise ValueError(f"Unknown AUDIO_SOURCE: {AUDIO_SOURCE}")

    # Wait for container to stop
    wait_for_container_stop()


# Start recording process.
run_recording()
