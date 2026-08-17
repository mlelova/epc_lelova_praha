#!/usr/bin/env bash
# Generate all thesis figures and tables by executing both analysis notebooks.
# Reads parquet inputs from data/tyndp2024/preprocessed/solved/ and writes
# JPG figures into tukedip_pdflatex_utf-8/figures/.
#
# Uses nbconvert --inplace so that the notebooks on disk are updated with
# fresh outputs (printed tables, inline plots).  Opening a notebook in
# Jupyter Lab afterwards will show all cell outputs without re-running.
#
# Takes ~5-10 minutes.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d venv ]]; then
    echo "venv/ not found.  Run scripts/setup.sh first."
    exit 1
fi

# shellcheck source=/dev/null
source venv/bin/activate

echo "=== 1/2: sensitivity_analysis.ipynb (chapters 5 & 6) ==="
jupyter nbconvert --to notebook --execute \
    scenario_analysis/sensitivity_analysis.ipynb --inplace

echo ""
echo "=== 2/2: peak_offpeak_analysis.ipynb (chapters 7 & 8) ==="
jupyter nbconvert --to notebook --execute \
    scenario_analysis/peak_offpeak_analysis.ipynb --inplace

echo ""
echo "Done."
echo "  Figures: tukedip_pdflatex_utf-8/figures/"
echo "  Tables:  open notebooks in 'jupyter lab', outputs are saved inline."
