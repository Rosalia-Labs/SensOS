#!/bin/bash -e

VENV_DIR="/sensos/python/venv"
REQ_FILE="/sensos/python/requirements.txt"
PYTHON="python3"

# Make sure the python venv and pip are installed
on_chroot <<EOF
set -e
echo "🔧 Creating virtual environment at: $VENV_DIR"
$PYTHON -m venv "$VENV_DIR"
echo "📦 Activating and installing from: $REQ_FILE"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r "$REQ_FILE"
echo "✅ Virtual environment ready: $VENV_DIR"
EOF

echo "Completed SensOS 07-run.sh"
