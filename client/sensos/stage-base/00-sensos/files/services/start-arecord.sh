#!/bin/bash
# start-arecord.sh: Launch continuous arecord segments using settings from $SENSOS_USER's home directory.
#
# This script expects that:
#   - SENSOS_USER is set in the environment.
#   - The configuration file exists at: <SENSOS_USER's home>/etc/arecord.conf
# The config file should define:
#   DEVICE, FORMAT, CHANNELS, RATE, MAX_TIME, and optionally BASE_DIR.
# If BASE_DIR is not set, it defaults to <SENSOS_USER's home>/sounds.
#
# The output files will be stored in a directory structure:
#   BASE_DIR/YYYY/MM/DD/sensos-YYYY-MM-DD-HH-MM-SS.wav

# Define the config file path based on SENSOS_HOME
CONFIG_FILE="/sensos/etc/arecord.conf"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Configuration file not found at $CONFIG_FILE"
    exit 1
fi

# Source configuration variables from the config file
source "$CONFIG_FILE"

# Verify that required variables are set
: "${DEVICE:?Missing DEVICE in config}"
: "${FORMAT:?Missing FORMAT in config}"
: "${CHANNELS:?Missing CHANNELS in config}"
: "${RATE:?Missing RATE in config}"
: "${MAX_TIME:?Missing MAX_TIME in config}"

# Set BASE_DIR default if not provided (use SENSOS_USER's home directory)
if [ -z "$BASE_DIR" ]; then
    BASE_DIR="/sensos/data/audio_recordings"
fi

# Define the output filename pattern.
# This pattern creates directories: BASE_DIR/YYYY/MM/DD/
# and filenames like: sensos-YYYY-MM-DD-HH-MM-SS.wav
OUTPUT_PATTERN="${BASE_DIR}/unprocessed/%Y/%m/%d/sensos_%Y%m%dT%H%M%S.wav"

# Ensure the base directory exists (arecord won't create intermediate directories)
mkdir -p "$BASE_DIR"

echo "Starting continuous recording with the following settings:"
echo "  DEVICE:   $DEVICE"
echo "  FORMAT:   $FORMAT"
echo "  CHANNELS: $CHANNELS"
echo "  RATE:     $RATE"
echo "  MAX_TIME: $MAX_TIME seconds"
echo "  OUTPUT:   $OUTPUT_PATTERN"
echo "Press Ctrl+C to stop."

exec arecord -D "$DEVICE" \
    -f "$FORMAT" \
    -c "$CHANNELS" \
    -r "$RATE" \
    --max-file-time="$MAX_TIME" \
    --use-strftime "$OUTPUT_PATTERN"
