#!/bin/bash
set -e

# Default to /sensos/python unless overridden
VENV_DIR="${VENV_DIR:-/sensos/python/venv}"
REQ_FILE="${REQ_FILE:-/sensos/python/requirements.txt}"
PYTHON="${PYTHON_BIN:-python3}"

echo "🔧 Creating virtual environment at: $VENV_DIR"
$PYTHON -m venv "$VENV_DIR"

echo "📦 Activating and installing from: $REQ_FILE"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r "$REQ_FILE"

echo "✅ Virtual environment ready: $VENV_DIR"
