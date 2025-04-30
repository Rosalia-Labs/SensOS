#!/bin/bash
set -e

# Get the absolute path to the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Absolute path to the /sensos/lib directory containing utils.py and test-utils.py
SENSOS_LIB_DIR="$SCRIPT_DIR/../stage-base/00-sensos/files/lib"

# Run in a transient Docker container
docker run --rm \
    -v "$SCRIPT_DIR":/test \
    -v "$SENSOS_LIB_DIR":/sensos_lib \
    -w /test \
    python:3.11-slim \
    bash -c "pip install -q pytest requests && pytest test-utils.py"
