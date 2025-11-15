# SensOS Analytics Server

The analytics server provides a centralized database for importing and analyzing recordings from multiple locations. Designed for bulk ingestion of TB-scale audio data collected from field sites.

## Use Case

- **Field deployments**: Clients (Raspberry Pi devices) collect audio continuously
- **Low bandwidth**: Cannot stream TB of data over limited connections  
- **Physical transfer**: Recordings collected on external drives, SD cards, etc.
- **Bulk import**: Ingest large directory structures (year/month/day) into centralized database
- **Location-based**: Track recordings by physical location (primary) with optional client linkage

## Architecture

### Containerized Python Environment

All Python code runs inside Docker containers, maintaining consistency with the SensOS codebase:

- **No host Python dependencies**: Admin scripts (`bulk-import`, etc.) run via `./bin/run-admin`
- **Isolated environments**: Each container has its own Python venv with pinned dependencies
- **Reproducible**: Same environment on all hosts

### Database Schema

**Primary identifier: Location** (not client registration)

- `sensos.locations` - Physical recording sites
  - `location_name` - Unique identifier (e.g., "backyard_feeder", "ridge_site_1")
  - `latitude`, `longitude` - Required for spatial queries
  - `elevation_m`, `notes` - Optional metadata
  - `client_uuid`, `client_wg_ip`, `client_hostname` - Optional linkage to management server
  - `last_import_at` - Track data freshness

- `sensos.audio_files` - Recordings with location tracking
  - `location_id` → `locations(id)`
  - `file_path` - Relative path preserving directory structure
  - `capture_timestamp`, audio metadata, etc.
  - Unique constraint: `(location_id, file_path)`

**BirdNET Analysis Tables** (created automatically):

- `sensos.audio_segments` - 3-second segments with 1-second steps
  - `file_id` → `audio_files(id)`
  - `channel`, `start_frame`, `end_frame`
  
- `sensos.birdnet_scores` - Species detection scores
  - `segment_id`, `label` (species name)
  - `score` (audio-based confidence)
  - `likely` (location-based likelihood, if lat/lon available)

- `sensos.birdnet_embeddings` - 1024-dimensional vectors for similarity
  - `segment_id`, `vector` (pgvector type)

- `sensos.sound_statistics` - Acoustic features per segment
  - `peak_amplitude`, `rms`, `snr`

- `sensos.full_spectrum` / `sensos.bioacoustic_spectrum` - Frequency analysis
  - Binned spectrograms stored as JSONB

- `sensos.score_statistics` - Diversity indices
  - `hill_number`, `simpson_index`

### Why Location-Based?

1. **Flexible**: Import data even without client registration
2. **Handles re-registration**: Clients may be updated and re-registered
3. **Multiple sources**: Can ingest non-SensOS data if you have a location
4. **Migrations**: Same physical site, different client hardware over time

## Setup

### 1. Configure Environment

```bash
cd server/analytics

# Specify custom paths for large data volumes
./bin/config-docker \
  --audio-dir /mnt/storage/audio_recordings \
  --db-data-path /mnt/storage/postgres \
  --postgres-password your_secure_password \
  --mkdirs
```

This creates `docker/.env` with your custom storage locations.

### 2. Start Services

```bash
./bin/start-docker
```

Starts:
- `sensos-analytics-database` - PostgreSQL with PostGIS and pgvector extensions
- `sensos-analytics-catalog-audio` - Audio cataloging service (location-aware)
- `sensos-analytics-birdnet` - BirdNET analysis service (extracts bird calls, embeddings, acoustic features)

Note: The `sensos-analytics-admin` container is available for running admin commands (bulk-import, etc.) but doesn't start automatically. Use `./bin/run-admin` to run containerized admin tasks.

**BirdNET Setup**: Before starting services, download the BirdNET model:

```bash
# Download and extract BirdNET v2.4 model
cd server/analytics/docker/birdnet/model
wget https://github.com/kahst/BirdNET-Analyzer/releases/download/v2.4/BirdNET_v2.4_tflite.zip
unzip BirdNET_v2.4_tflite.zip
# Expected structure:
#   model/BirdNET_v2.4_tflite/audio-model.tflite
#   model/BirdNET_v2.4_tflite/meta-model.tflite
#   model/BirdNET_v2.4_tflite/labels/en_us.txt
```

### 2a. Optional: manage via systemd

Install unit on a Linux host (paths assume repo at `/home/sensos/SensOS` and user `sensos`):

```bash
# Copy unit file
sudo cp server/analytics/etc/systemd/system/sensos-analytics.service /etc/systemd/system/

# Reload units and enable
sudo systemctl daemon-reload
sudo systemctl enable --now sensos-analytics.service

# Check status and logs
systemctl status sensos-analytics.service
journalctl -u sensos-analytics.service -f
```

Systemd unit uses these helper scripts:

- `server/analytics/bin/start-analytics.sh` – starts docker compose with `docker/.env`
- `server/analytics/bin/stop-analytics.sh` – stops stack; optional `--backup` and `--remove-volumes`
- `server/analytics/bin/backup-analytics-database.sh` – runs `pg_dumpall` from the container

### 3. Bulk Import Recordings

Import large directory structures with year/month/day organization using the containerized admin tools:

```bash
# Import from typical client data structure
# biosense_1_14/sensos/data/audio_recordings/cataloged/2025/08/08/*.flac
./bin/run-admin /app/bulk-import \
  --source /audio_recordings/source_data \
  --location "biosense_site_1_14" \
  --lat -2.8894 --lon -60.6348 \
  --notes "Biosense deployment 1.14, Amazon rainforest"

# Note: Mount source directory into container by adding it to docker/.env:
# AUDIO_DIR=/mnt/storage/audio_recordings
# Then place source data under that path or mount additional volumes

# With client linkage (optional)
./bin/run-admin /app/bulk-import \
  --source /audio_recordings/client1_backup \
  --location "ridge_site_1" \
  --lat 36.7783 --lon -119.4179 \
  --client-uuid "a1b2c3d4-..." \
  --client-hostname "sensos-pi-001"

# Dry run first to preview (recommended for TB-scale imports)
./bin/run-admin /app/bulk-import \
  --source /audio_recordings/test_data \
  --location "test_site" \
  --lat 0 --lon 0 \
  --dry-run
```

**Important**: The `bulk-import` script runs inside a container (`sensos-analytics-admin`). The `--source` path must be accessible from within the container:

- By default, `${AUDIO_DIR}` is mounted at `/audio_recordings` in the container
- Place your source data within `${AUDIO_DIR}` or mount additional volumes in `docker-compose.yml`
- Example: If `AUDIO_DIR=/mnt/storage/audio_recordings`, place imports under `/mnt/storage/audio_recordings/imports/`

**File Format Handling**:

- **FLAC files**: Copied directly to `<location>/cataloged/YYYY/MM/DD/` and registered in database immediately
- **WAV files**: Placed in `<location>/queued/YYYY/MM/DD/` for background conversion by catalog service
  - Cataloger converts WAV→FLAC and moves to `cataloged/`
  - Cataloger fills in audio metadata (frames, channels, sample rate, etc.)
  - Allows fast import without waiting for conversion
- **Pattern matching**: Only `sensos_YYYYMMDDTHHMMSS.*` files are imported; others are ignored

**Alternative: Interactive admin container**

For multiple operations or troubleshooting, run an interactive shell:

```bash
# Start interactive admin container
./bin/run-admin /bin/bash

# Inside container, run bulk-import directly
/app/bulk-import --source /audio_recordings/data --location site_1 --lat 0 --lon 0 --dry-run
/app/bulk-import --source /audio_recordings/data --location site_1 --lat 0 --lon 0

# Or connect to database directly
psql -h sensos-analytics-database -U sensos -d sensos
```

# Example dry-run output:
# Found 96 sensos audio files
# Would import 96 files to location 'biosense_site_1_14'
# Sample files (first 10):
#   sensos/data/audio_recordings/cataloged/2025/08/08/sensos_20250808T001404.flac -> 2025-08-08 00:14:04
#   sensos/data/audio_recordings/cataloged/2025/08/08/sensos_20250808T002904.flac -> 2025-08-08 00:29:04
#   ...
```

**Important**: The script only imports files matching the pattern `sensos_YYYYMMDDTHHMMSS.[flac|wav|mp3|ogg]`. Files like `Barn_Owl_Tyto_alba__0.998_0.170_2405393.flac` (bird detection examples) are automatically ignored.

The script handles different formats:
- **FLAC**: Copied directly to `cataloged/YYYY/MM/DD/`, registered immediately in database
- **WAV**: Placed in `queued/YYYY/MM/DD/` for background conversion to FLAC by catalog service
- **MP3/OGG**: Placed in `queued/YYYY/MM/DD/` for background conversion (if supported by soundfile)

Workflow:
- Recursively scans for files matching `sensos_YYYYMMDDTHHMMSS` pattern
- Parses capture timestamp from filename (e.g., `sensos_20250408T001404.wav` → 2025-04-08 00:14:04)
- FLAC: Copies to `<AUDIO_DIR>/<location>/cataloged/YYYY/MM/DD/` and inserts DB row immediately
- WAV: Copies to `<AUDIO_DIR>/<location>/queued/YYYY/MM/DD/` for cataloger to process
- Cataloger converts queued files to FLAC, enriches metadata, and registers in database
- Shows progress for large imports
- Processes in batches (1000 files/transaction by default)
- Skips already-imported files (idempotent)

## Workflow Summary

1. **Collect recordings**: Field sites record continuously to local storage (FLAC or WAV)
2. **Physical transfer**: Copy recordings to analytics server (external drive, rsync, etc.)
3. **Configure storage**: Use `config-docker` to set custom paths for TB-scale data
4. **Start services**: Use `start-docker` to bring up database, catalog service, and BirdNET analyzer
5. **Bulk import**: 
   - Use `bulk-import` to copy files by location
   - FLAC files registered immediately in database
   - WAV files staged in `queued/` for background conversion
6. **Background processing**: Catalog service converts WAV→FLAC, enriches metadata, registers in DB
7. **BirdNET analysis**: Automatically processes cataloged files, extracting:
   - Bird species detection scores (top 5 per 3-second segment)
   - 1024-dimensional embeddings for similarity search
   - Acoustic features (spectrogram, RMS, SNR)
   - Diversity indices (Hill number, Simpson index)
8. **Query**: Use SQL for spatial/temporal analysis across locations

## BirdNET Analysis

The BirdNET service automatically processes all cataloged audio files:

### What It Computes

For each 3-second audio segment (with 1-second steps):
- **BirdNET scores**: Top 5 species detections with confidence scores
- **Location likelihood**: If lat/lon provided, locality probability for each species
- **Embeddings**: 1024-dimensional vector for similarity search
- **Acoustic features**: 
  - Full spectrum (50 Hz - 24 kHz, 20 bins)
  - Bioacoustic spectrum (1-8 kHz, 20 bins)
  - Sound statistics (peak amplitude, RMS, SNR)
- **Diversity**: Hill number and Simpson index

### Example Queries

```sql
-- Find highest confidence bird detections at a location
SELECT 
  l.location_name,
  bs.label,
  bs.score,
  bs.likely,
  af.capture_timestamp
FROM sensos.birdnet_scores bs
JOIN sensos.audio_segments seg ON bs.segment_id = seg.id
JOIN sensos.audio_files af ON seg.file_id = af.id
JOIN sensos.locations l ON af.location_id = l.id
WHERE l.location_name = 'site_001_oak_woodland'
  AND bs.score > 0.8
ORDER BY bs.score DESC
LIMIT 10;

-- Compare species diversity across locations
SELECT 
  l.location_name,
  AVG(ss.hill_number) as avg_diversity,
  COUNT(DISTINCT seg.id) as segment_count
FROM sensos.score_statistics ss
JOIN sensos.audio_segments seg ON ss.segment_id = seg.id
JOIN sensos.audio_files af ON seg.file_id = af.id
JOIN sensos.locations l ON af.location_id = l.id
GROUP BY l.location_name
ORDER BY avg_diversity DESC;

-- Find similar audio segments using embeddings
SELECT 
  seg.id,
  af.file_path,
  af.capture_timestamp,
  be.vector <-> (SELECT vector FROM sensos.birdnet_embeddings WHERE segment_id = 12345) as distance
FROM sensos.birdnet_embeddings be
JOIN sensos.audio_segments seg ON be.segment_id = seg.id
JOIN sensos.audio_files af ON seg.file_id = af.id
WHERE seg.id != 12345
ORDER BY distance
LIMIT 10;
```

### Monitoring

Watch BirdNET processing in real-time:
```bash
docker logs -f sensos-analytics-birdnet
```

## Example Import Session

```bash
# Initial setup (once)
cd server/analytics
./bin/config-docker \
  --audio-dir /data/sensos/audio \
  --db-data-path /data/sensos/postgres \
  --postgres-password "$(openssl rand -base64 32)" \
  --mkdirs

./bin/start-docker

# Wait for database to initialize
docker logs -f sensos-analytics-database
# (Ctrl-C when you see "database system is ready")

# Copy source data into accessible location
# The admin container has AUDIO_DIR mounted at /audio_recordings
sudo mkdir -p /data/sensos/audio/import_staging
sudo rsync -av /mnt/backup1/ /data/sensos/audio/import_staging/site1/
sudo rsync -av /mnt/backup2/ /data/sensos/audio/import_staging/site2/

# Import data from first location (using containerized admin)
./bin/run-admin /app/bulk-import \
  --source /audio_recordings/import_staging/site1 \
  --location "site_001_oak_woodland" \
  --lat 37.8715 --lon -122.2730 \
  --elevation 100 \
  --notes "Oak woodland site, recorder deployed 2024-01"

# Import complete!
#   Total imported:     1234
#   Files copied:       1234
#   Already existed:    0
#   Skipped:            56
#   Errors:             0
#
# Note: WAV files are placed in queued/ for background
#       conversion to FLAC by the catalog service.

# Watch catalog service convert WAV files in background
docker logs -f sensos-analytics-catalog-audio

# Import data from second location (WAV files from sensor)
./bin/run-admin /app/bulk-import \
  --source /audio_recordings/import_staging/bfl_1_04_17_25/audio_recordings/unprocessed \
  --location "site_002_riparian" \
  --lat 37.8650 --lon -122.2680 \
  --elevation 50 \
  --notes "Riparian corridor, WAV data from April 2025"

# Clean up staging area after import
sudo rm -rf /data/sensos/audio/import_staging/
```

## Querying Location-Based Data

```sql
-- List all locations
SELECT 
  id, 
  location_name, 
  latitude, 
  longitude,
  COUNT(*) FILTER (WHERE NOT deleted) as file_count,
  last_import_at
FROM sensos.locations l
LEFT JOIN sensos.audio_files af ON af.location_id = l.id
GROUP BY l.id
ORDER BY location_name;

-- Find recordings by location and date range
SELECT 
  l.location_name,
  af.file_path,
  af.capture_timestamp,
  af.frames / af.sample_rate / 3600.0 as hours
FROM sensos.audio_files af
JOIN sensos.locations l ON l.id = af.location_id
WHERE l.location_name = 'backyard_feeder'
  AND af.capture_timestamp BETWEEN '2024-01-01' AND '2024-04-01'
  AND af.deleted = FALSE
ORDER BY af.capture_timestamp;

-- Spatial query: recordings within distance
SELECT 
  l.location_name,
  COUNT(*) as recordings,
  ST_Distance(
    ST_SetSRID(ST_MakePoint(l.longitude, l.latitude), 4326)::geography,
    ST_SetSRID(ST_MakePoint(-122.27, 37.87), 4326)::geography
  ) / 1000 as distance_km
FROM sensos.locations l
JOIN sensos.audio_files af ON af.location_id = l.id
WHERE af.deleted = FALSE
GROUP BY l.id, l.location_name, l.latitude, l.longitude
HAVING ST_Distance(
  ST_SetSRID(ST_MakePoint(l.longitude, l.latitude), 4326)::geography,
  ST_SetSRID(ST_MakePoint(-122.27, 37.87), 4326)::geography
) < 5000  -- within 5km
ORDER BY distance_km;
```

## Storage Planning

For TB-scale deployments:

- **Audio files**: ~1 GB/hour for stereo 48kHz FLAC
- **Database**: ~1 MB per 1000 files (metadata only)
- **Recommendations**:
  - Use separate large volumes for `--audio-dir` and `--db-data-path`
  - SSD for database, HDD acceptable for audio files
  - Plan for 2-3x source data size (working copies during processing)

## Next Steps

After bulk import, you can:

1. **Add analysis containers** (BirdNET, etc.) to `docker-compose.yml`
2. **Query spatial patterns** using PostGIS functions
3. **Export subsets** for detailed analysis
4. **Link to management server** by adding client_uuid after import

## Troubleshooting

### Database not starting

Check logs:
```bash
docker logs sensos-analytics-database
```

Verify PostgreSQL extensions installed:
```bash
docker exec sensos-analytics-database psql -U sensos -c "\dx"
```

### Bulk import issues

**"Source directory does not exist"**: 
- Source path is relative to container filesystem, not host
- Ensure source data is under `${AUDIO_DIR}` which is mounted at `/audio_recordings` in container
- Or add additional volume mounts in `docker-compose.yml`

**"Failed to connect to database"**: 
- Verify service is running: `docker ps`
- Check if using `./bin/run-admin` (automatically connects to correct network)
- Database hostname should be `sensos-analytics-database` (not `localhost`)

**"Module not found" errors**:
- Run `docker compose build sensos-analytics-admin` to rebuild container
- Admin container uses isolated Python environment inside Docker

**Large import times**: Use `--batch-size` to tune transaction size. Default is 1000 files/batch.

### Storage running out

Check disk usage:
```bash
df -h /data/sensos/audio
df -h /data/sensos/postgres
```

Clean up with:
```sql
-- Find largest locations
SELECT location_name, COUNT(*) as files
FROM sensos.audio_files af
JOIN sensos.locations l ON l.id = af.location_id
GROUP BY location_name
ORDER BY files DESC;

-- Delete old data (be careful!)
DELETE FROM sensos.audio_files 
WHERE location_id = X AND capture_timestamp < '2023-01-01';
```

## Security Notes

- Keep database passwords secure (use generated passwords, not defaults)
- Limit database network exposure (bind to localhost or VPN)
- Regular backups of database: `docker exec sensos-analytics-database pg_dump ...`
- Audit location table for unexpected entries
- Set appropriate file permissions on audio directories

### Database connection issues

Verify database is running:
```bash
docker ps
docker logs sensos-analytics-database
```

Check credentials in `docker/.env`

## Security Notes

- Keep management server credentials secure
- Use strong passwords for analytics database
- Consider VPN/SSH tunnel for management server access
- Backup analytics database regularly
- Audit import history: `SELECT * FROM sensos.import_history;`
