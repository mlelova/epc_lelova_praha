#!/usr/bin/env bash
# Full replication pipeline: raw data -> built networks -> solved networks -> parquet.
# Requires a working Gurobi licence.  Takes ~30 hours on a 32 GB RAM server.
#
# Steps:
#   1. Convert XLSX/CSV sources to Parquet       (~10 min, one-time)
#   2. Build 432 PyPSA scenario networks          (~hours)
#   3. Solve all 432 networks with Gurobi         (~30 h)
#   4. Extract results to Parquet for notebooks   (~minutes)

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d venv ]]; then
    echo "venv/ not found.  Run scripts/setup.sh first."
    exit 1
fi

# shellcheck source=/dev/null
source venv/bin/activate

echo "=== Step 1/4: preprocess_xlsx.py ==="
python scenarios/preprocess_xlsx.py

echo ""
echo "=== Step 2/4: run_scenarios.py ==="
python scenarios/run_scenarios.py --matrix core --workers 4

echo ""
echo "=== Step 3/4: solve_scenarios.py (Gurobi, long) ==="
python scenarios/solve_scenarios.py \
    --networks-dir scenarios/networks_core \
    --output-dir solved_networks_core \
    --workers 2 \
    --threads 3

echo ""
echo "=== Step 4/4: preprocess_networks.py ==="
python scenarios/preprocess_networks.py \
    --solved-dir solved_networks_core \
    --out-dir data/tyndp2024/preprocessed/solved

echo ""
echo "Pipeline complete.  Run scripts/run_analysis.sh to generate thesis figures."
