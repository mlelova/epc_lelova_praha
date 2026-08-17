#!/usr/bin/env bash
# One-time Python environment setup.
# Creates venv/ in the project root and installs requirements.txt.
# Safe to re-run: reuses existing venv/ if present.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d venv ]]; then
    echo "[1/3] Creating venv..."
    python3 -m venv venv
else
    echo "[1/3] venv/ already exists, reusing."
fi

# shellcheck source=/dev/null
source venv/bin/activate

echo "[2/3] Upgrading pip..."
pip install --upgrade pip --quiet

echo "[3/3] Installing requirements.txt..."
pip install -r requirements.txt --quiet

echo ""
echo "Setup complete."
echo "Activate with:  source venv/bin/activate"
