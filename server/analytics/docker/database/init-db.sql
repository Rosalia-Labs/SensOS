-- SPDX-License-Identifier: MIT
-- Copyright (c) 2025 Rosalia Labs LLC

-- Initialize analytics database schema
-- This schema is similar to the client schema but adds client_id tracking

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS sensos;

-- Set default search path
ALTER DATABASE sensos SET search_path TO sensos, public;

-- ============================================================================
-- CLIENTS TABLE
-- Store information about clients whose data is imported
-- This links to the management server's wireguard_peers table
-- ============================================================================
CREATE TABLE IF NOT EXISTS sensos.clients (
    id SERIAL PRIMARY KEY,
    -- Management server reference
    peer_uuid UUID UNIQUE NOT NULL,
    wg_ip INET UNIQUE NOT NULL,
    
    -- Client identification
    hostname TEXT,
    note TEXT,
    
    -- Metadata
    registered_at TIMESTAMPTZ DEFAULT NOW(),
    last_import_at TIMESTAMPTZ,
    
    -- Optional: hardware profile snapshot
    model TEXT,
    kernel_version TEXT,
    cpu_info JSONB,
    memory_info JSONB,
    
    -- Optional: location
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    location_updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS clients_peer_uuid_idx ON sensos.clients(peer_uuid);
CREATE INDEX IF NOT EXISTS clients_wg_ip_idx ON sensos.clients(wg_ip);
CREATE INDEX IF NOT EXISTS clients_hostname_idx ON sensos.clients(hostname);

-- ============================================================================
-- AUDIO FILES TABLE
-- Enhanced with client_id to track which client recorded the audio
-- ============================================================================
CREATE TABLE IF NOT EXISTS sensos.audio_files (
    id SERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES sensos.clients(id) ON DELETE CASCADE,
    
    -- File information (relative path within client's recordings)
    file_path TEXT NOT NULL,
    
    -- Audio metadata
    frames BIGINT,
    channels INTEGER,
    sample_rate INTEGER,
    format TEXT,
    subtype TEXT,
    
    -- Timestamps
    capture_timestamp TIMESTAMPTZ,
    cataloged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Deletion tracking
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    
    -- Ensure unique file per client
    UNIQUE(client_id, file_path)
);

CREATE INDEX IF NOT EXISTS audio_files_client_id_idx ON sensos.audio_files(client_id);
CREATE INDEX IF NOT EXISTS audio_files_file_path_idx ON sensos.audio_files(file_path);
CREATE INDEX IF NOT EXISTS audio_files_capture_timestamp_idx ON sensos.audio_files(capture_timestamp);
CREATE INDEX IF NOT EXISTS audio_files_client_capture_idx ON sensos.audio_files(client_id, capture_timestamp);
CREATE INDEX IF NOT EXISTS audio_files_channels_idx ON sensos.audio_files(capture_timestamp, channels);
CREATE INDEX IF NOT EXISTS audio_files_deleted_idx ON sensos.audio_files(deleted);

-- ============================================================================
-- AUDIO SEGMENTS TABLE
-- Audio analysis segments with client tracking
-- ============================================================================
CREATE TABLE IF NOT EXISTS sensos.audio_segments (
    id SERIAL PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES sensos.audio_files(id) ON DELETE CASCADE,
    channel INT NOT NULL,
    start_frame BIGINT NOT NULL,
    end_frame BIGINT NOT NULL CHECK (end_frame > start_frame),
    zeroed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (file_id, channel, start_frame)
);

CREATE INDEX IF NOT EXISTS audio_segments_file_id_idx ON sensos.audio_segments(file_id);
CREATE INDEX IF NOT EXISTS audio_segments_file_start_idx ON sensos.audio_segments(file_id, start_frame);
CREATE INDEX IF NOT EXISTS audio_segments_processed_idx ON sensos.audio_segments(processed);

-- ============================================================================
-- SOUND STATISTICS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS sensos.sound_statistics (
    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
    peak_amplitude FLOAT,
    rms FLOAT,
    snr FLOAT
);

-- ============================================================================
-- SPECTRUM TABLES
-- ============================================================================
CREATE TABLE IF NOT EXISTS sensos.full_spectrum (
    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
    spectrum JSONB
);

CREATE TABLE IF NOT EXISTS sensos.bioacoustic_spectrum (
    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
    spectrum JSONB
);

-- ============================================================================
-- BIRDNET TABLES
-- ============================================================================
CREATE TABLE IF NOT EXISTS sensos.birdnet_embeddings (
    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
    vector vector(1024)
);

CREATE TABLE IF NOT EXISTS sensos.birdnet_scores (
    segment_id INTEGER REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    score FLOAT NOT NULL,
    likely FLOAT,
    PRIMARY KEY (segment_id, label)
);

CREATE INDEX IF NOT EXISTS birdnet_scores_segment_idx ON sensos.birdnet_scores(segment_id);
CREATE INDEX IF NOT EXISTS birdnet_scores_label_idx ON sensos.birdnet_scores(label);
CREATE INDEX IF NOT EXISTS birdnet_scores_score_idx ON sensos.birdnet_scores(score DESC);

CREATE TABLE IF NOT EXISTS sensos.score_statistics (
    segment_id INTEGER PRIMARY KEY REFERENCES sensos.audio_segments(id) ON DELETE CASCADE,
    hill_number FLOAT,
    simpson_index FLOAT
);

CREATE TABLE IF NOT EXISTS sensos.birdnet_processed_files (
    file_id INTEGER PRIMARY KEY REFERENCES sensos.audio_files(id) ON DELETE CASCADE,
    segment_count INTEGER NOT NULL CHECK (segment_count >= 0),
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- I2C SENSOR READINGS TABLE
-- Environmental sensor data with client tracking
-- ============================================================================
CREATE TABLE IF NOT EXISTS sensos.i2c_readings (
    id SERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES sensos.clients(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL,
    
    -- Sensor readings
    temperature_c REAL,
    pressure_hpa REAL,
    humidity_percent REAL,
    light_lux REAL,
    
    -- Indexing for time-series queries
    UNIQUE(client_id, timestamp)
);

CREATE INDEX IF NOT EXISTS i2c_readings_client_id_idx ON sensos.i2c_readings(client_id);
CREATE INDEX IF NOT EXISTS i2c_readings_timestamp_idx ON sensos.i2c_readings(timestamp);
CREATE INDEX IF NOT EXISTS i2c_readings_client_timestamp_idx ON sensos.i2c_readings(client_id, timestamp);

-- ============================================================================
-- SYSTEM STATISTICS TABLE
-- System performance metrics with client tracking
-- ============================================================================
CREATE TABLE IF NOT EXISTS sensos.system_stats (
    id SERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES sensos.clients(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL,
    
    -- System metrics
    cpu_percent REAL,
    memory_percent REAL,
    disk_percent REAL,
    temperature_c REAL,
    load_1m REAL,
    load_5m REAL,
    load_15m REAL,
    
    UNIQUE(client_id, timestamp)
);

CREATE INDEX IF NOT EXISTS system_stats_client_id_idx ON sensos.system_stats(client_id);
CREATE INDEX IF NOT EXISTS system_stats_timestamp_idx ON sensos.system_stats(timestamp);
CREATE INDEX IF NOT EXISTS system_stats_client_timestamp_idx ON sensos.system_stats(client_id, timestamp);

-- ============================================================================
-- IMPORT TRACKING TABLE
-- Track import operations from clients
-- ============================================================================
CREATE TABLE IF NOT EXISTS sensos.import_history (
    id SERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES sensos.clients(id) ON DELETE CASCADE,
    import_type TEXT NOT NULL, -- 'audio', 'sensors', 'system_stats', 'full'
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running', -- 'running', 'completed', 'failed'
    records_imported INTEGER DEFAULT 0,
    error_message TEXT,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS import_history_client_id_idx ON sensos.import_history(client_id);
CREATE INDEX IF NOT EXISTS import_history_started_at_idx ON sensos.import_history(started_at);
CREATE INDEX IF NOT EXISTS import_history_status_idx ON sensos.import_history(status);
