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

# Check if SENSOS_USER is set
if [ -z "$SENSOS_USER" ]; then
    echo "Error: SENSOS_USER is not set. Please set the SENSOS_USER environment variable."
    exit 1
fi

# Compute the home directory of SENSOS_USER
SENSOS_HOME=$(eval echo "~$SENSOS_USER")

# Define the config file path based on SENSOS_HOME
CONFIG_FILE="$SENSOS_HOME/etc/arecord.conf"

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
    BASE_DIR="$SENSOS_HOME/sounds"
fi

# Define the output filename pattern.
# This pattern creates directories: BASE_DIR/YYYY/MM/DD/
# and filenames like: sensos-YYYY-MM-DD-HH-MM-SS.wav
OUTPUT_PATTERN="${BASE_DIR}/%Y/%m/%d/sensos-%Y-%m-%d-%H-%M-%S.wav"

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

# Loop continuously launching arecord for each segment
while true; do
    echo "Starting new recording segment..."
    arecord -D "$DEVICE" \
        -f "$FORMAT" \
        -c "$CHANNELS" \
        -r "$RATE" \
        --max-time="$MAX_TIME" \
        --use-strftime "$OUTPUT_PATTERN"
    # If arecord exits with an error, pause briefly before restarting
    if [ $? -ne 0 ]; then
        echo "arecord encountered an error; waiting 5 seconds before retrying..."
        sleep 5
    fi
done
