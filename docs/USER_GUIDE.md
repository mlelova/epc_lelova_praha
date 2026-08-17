# User Guide

How to reproduce the thesis results from scratch or from pre-solved data.

This guide assumes macOS or Linux with a Unix shell (`bash` / `zsh`).
All commands are run from the project root unless noted otherwise.

---

## 1. Prerequisites

- **Operating system:** macOS or Linux
- **Python:** 3.10 or newer
- **Disk space:**
    - Shortcut path: ~25 GB (pre-solved networks + parquet)
    - Full replication: ~35 GB (raw TYNDP data + intermediates + solved networks)
- **RAM:** 16 GB is enough to run the notebooks; solving one scenario with Gurobi uses ~28 GB, so the solve step requires a machine with ≥ 32 GB RAM
- **Gurobi:** free academic license (see section 4)

You do **not** need Gurobi if you follow the shortcut path in section 5.1.

---

## 2. Installation

### 2.1 Clone the repository

```bash
git clone <REPO-URL-TBD>
cd "Final diplomova praca"
```

### 2.2 Python environment (Option A — `venv`, primary path)

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2.3 Python environment (Option B — `uv`, faster alternative)

[`uv`](https://docs.astral.sh/uv/) installs the same dependencies ~10×
faster:

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 2.4 Verify installation

```bash
python -c "import pypsa, pandas, matplotlib, statsmodels; print('OK')"
```

The output must be `OK`. If anything fails, re-check Python version and
activated environment.

---

## 3. Data setup

The project uses three datasets stored in three locations. Their size
and origin differ, so they are distributed differently.

### 3.1 Derived datasets — `data/open-tyndp/`

These are the processed inputs used directly by `scenarios/build_network.py`
(bus definitions, carriers, cost data, technology maps, node tables).
They are shipped with the repository as compressed archives to keep the
git checkout small.

```bash
cd data
tar -xzf open-tyndp.tar.gz
cd ..
```

After extraction you should see `data/open-tyndp/buses.csv`,
`data/open-tyndp/carriers_2030.csv`, `data/open-tyndp/technologies_2030.csv`
and other files.

### 3.2 Raw TYNDP 2024 data — `data/tyndp2024/`

These are the original ENTSO-E publications (~8.5 GB): PECD 2030 climate
profiles, PEMMDB2 capacity database, demand profiles, hydro inflows, and
the Plexos MMStandardOutputFile for validation. They are too large for
git.

Download the archive from `<LINK-TBD>` and extract:

```bash
cd data
tar -xf tyndp2024.tar.gz
cd ..
```

You should end up with the following structure:

```
data/tyndp2024/
├── 250117_TYNDP2024Scenarios_Electricity_Demand.csv
├── 250117_TYNDP2024Scenarios_Electricity_Flexibility.csv
├── 250117_TYNDP2024Scenarios_Electricity_SupplyMix.csv
├── Demand Profiles/
├── Hydro Inflows/
├── Investment Dataset/
├── MMStandardOutputFile_NT2030_Plexos_CY2009_2.5_v40 3.xlsx
├── PECD 2030/
└── PEMMDB2/
```

If you prefer to download the files directly from ENTSO-E, navigate to
<https://2024.entsos-tyndp-scenarios.eu/download/> and grab the same
content; the archive is a convenience mirror.

### 3.3 Pre-solved networks — `solved_networks_core/` (shortcut path only)

If you want to skip the ~30-hour Gurobi solve step, download the archive
of solved networks from `<LINK-TBD>` (~22 GB) and extract to the
project root:

```bash
tar -xf solved_networks_core.tar.gz
```

You should have `solved_networks_core/` with 432 `.nc` files plus
`solve_results.csv`, and `data/tyndp2024/preprocessed/solved/` with
parquet outputs consumed by the notebooks.

Skip this section if you intend to run the full solve yourself.

---

## 4. Gurobi license setup

Needed only for the full replication path. Skip to section 5.1 if you
use pre-solved data.

### 4.1 Register for an academic licence

Create an account at <https://www.gurobi.com/features/academic-named-user-license/>
using your **university email address**. Request an academic "Named
User" license.

### 4.2 Retrieve the license key

After approval, Gurobi will send a license ID. Install the Gurobi
command-line tools (they come bundled with the `gurobipy` pip package),
then run:

```bash
grbgetkey <LICENSE-ID>
```

`grbgetkey` requires you to be on your university network or connected
via VPN during this one-time step.

### 4.3 License file placement

By default the key is written to `~/gurobi.lic`. To use a custom path
set the environment variable:

```bash
export GRB_LICENSE_FILE=/custom/path/gurobi.lic
```

(Put this in your `~/.bashrc` or `~/.zshrc` to make it permanent.)

### 4.4 Verify

```bash
gurobi_cl --license
```

The output must show a valid academic license with your name and
expiration date. If it reports "No license file found", verify the path
or environment variable.

---

## 5. Running the pipeline

Two paths are supported. Pick one.

### 5.1 Shortcut path — use pre-solved data

After sections 2 and 3.3 are complete, everything you need for analysis
is already on disk. Go directly to section 6.

Sanity check:

```bash
ls solved_networks_core/*.nc | wc -l                 # 432
ls data/tyndp2024/preprocessed/solved/*.parquet | wc -l  # 6–8 files
```

### 5.2 Full replication path — solve from scratch

Four steps, each run from the project root with the venv activated.

#### Step 1 — Convert source files to Parquet

One-time conversion of slow XLSX and PECD CSV files to fast Parquet for
subsequent loads (~10 minutes, ~1 GB of output):

```bash
python scenarios/preprocess_xlsx.py
```

Expected console output (abridged):

```
[1/3] demand_profiles.parquet ... done
[2/3] hydro_inflows.parquet ... done
[3/3] PECD profiles (onshore wind, offshore wind, solar utility/rooftop/generic) ... done
```

Outputs land in `data/tyndp2024/preprocessed/*.parquet`.

#### Step 2 — Build the 432 scenario networks

Builds unsolved PyPSA networks for every parameter combination in the
factorial matrix (~hours, depending on CPU and I/O):

```bash
python scenarios/run_scenarios.py --matrix core --workers 4
```

Key flags:

- `--matrix core` — only the 432 core scenarios (default: all matrices;
  alternate matrices `nuclear`, `demand`, `ntc`, `invest` are optional
  experiments not used in the thesis)
- `--workers N` — parallel worker processes (default 4)
- `--dry-run` — print the scenario plan and write the manifest without
  building
- `--remove-old` — wipe the target `networks_<matrix>/` directory first

Output: `scenarios/networks_core/<tag>.nc` for every scenario, plus
`scenarios/networks_core/manifest.csv`.

#### Step 3 — Solve the 432 networks with Gurobi

Runs Gurobi on every network file from Step 2 (~30 hours on a
server-class machine with 32 GB RAM and two parallel workers):

```bash
python scenarios/solve_scenarios.py \
    --networks-dir scenarios/networks_core \
    --output-dir solved_networks_core \
    --workers 2 \
    --threads 3
```

Key flags:

- `--networks-dir PATH` — where to read unsolved `.nc` files
- `--output-dir PATH` — where to write solved `.nc` files (default
  `solved_networks/`, use `solved_networks_core` to match the rest of
  the tooling)
- `--workers N` — parallel Gurobi processes (default 1; each uses ~28 GB
  RAM)
- `--threads N` — Gurobi threads per worker (default 2)
- `--tag TAG` — solve a single named scenario (handy for debugging)
- `--force` — re-solve scenarios that already have output files

Output: `solved_networks_core/<tag>.nc` + `solved_networks_core/<tag>_gurobi.log`
for every scenario, plus a running `solved_networks_core/solve_results.csv`
manifest with status, solve time, and slack MWh per scenario.

#### Step 4 — Extract solved results to Parquet

Converts the 432 solved NetCDF files into column-oriented Parquet tables
that the analysis notebooks consume (~minutes):

```bash
python scenarios/preprocess_networks.py \
    --solved-dir solved_networks_core \
    --out-dir data/tyndp2024/preprocessed/solved
```

Key flags:

- `--solved-dir PATH` — where to read solved `.nc` files
- `--out-dir PATH` — where to write parquet outputs

Output: `data/tyndp2024/preprocessed/solved/{lmp,gen_p,storage_p,links_p0,static_generators,static_storage_units,static_links}.parquet`.

---

## 6. Running the analysis notebooks

The two notebooks in `scenario_analysis/` generate every figure used in
the thesis and print the numeric values for the thesis tables.

Two equivalent ways to run the notebooks. Pick whichever fits your
workflow.

### 6.1 Option A — headless execution from the terminal

Best for reproducing all figures in one shot without opening a browser:

```bash
source venv/bin/activate         # if not already active
jupyter execute scenario_analysis/sensitivity_analysis.ipynb
jupyter execute scenario_analysis/peak_offpeak_analysis.ipynb
```

`jupyter execute` runs every cell in order and fails loudly if any cell
raises an exception. Outputs are **not** written back into the notebook
file itself — the notebook on disk is unchanged — but `save_fig(...)`
calls still write JPGs into `tukedip_pdflatex_utf-8/figures/` and
`print(...)` output goes to the terminal.

If you want the executed notebook to be saved with fresh outputs (handy
for archiving), use `nbconvert --inplace` instead:

```bash
jupyter nbconvert --to notebook --execute \
    scenario_analysis/sensitivity_analysis.ipynb --inplace
jupyter nbconvert --to notebook --execute \
    scenario_analysis/peak_offpeak_analysis.ipynb --inplace
```

### 6.2 Option B — interactive Jupyter Lab in the browser

Best for stepping through cells, inspecting intermediate variables, or
tweaking plots:

```bash
source venv/bin/activate
jupyter lab
```

Navigate to `scenario_analysis/` in the browser. Open each notebook in
turn and run *Kernel → Restart Kernel and Run All Cells*.

### 6.3 Notebook order

Both notebooks are independent and can be run in any order, but the
natural reading order follows the thesis chapters:

1. **`sensitivity_analysis.ipynb`** — produces chapters 5 and 6 outputs.
2. **`peak_offpeak_analysis.ipynb`** — produces chapters 7 and 8 outputs.

### 6.4 Expected outputs

Generated figures are saved directly into
`tukedip_pdflatex_utf-8/figures/` so that a subsequent LaTeX rebuild
picks them up. Numeric values for thesis tables appear as printed output
inside the notebook.

**`sensitivity_analysis.ipynb`** produces:

| Thesis reference | Kind | File or source |
|---|---|---|
| Table `t:anova_summary` | table | printed ANOVA output (DE00 + EU cells) |
| Table `t:ols_coefs` | table | printed OLS coefficients (DE00 + EU cells) |
| Table `t:spillover` | table | printed DE00 + DKE1 stats |
| `fig_spatial_cv.jpg` | figure | `tukedip_pdflatex_utf-8/figures/` |
| `fig_merit_order_pct_bat{2,4,8}.jpg` | figures | same |
| `fig_import_change_pct_bat{2,4,8}.jpg` | figures | same |
| `fig_price_correlation.jpg` | figure | same |
| `fig_eu_cv_vs_batscale.jpg` | figure | same |

**`peak_offpeak_analysis.ipynb`** produces:

| Thesis reference | Kind | File or source |
|---|---|---|
| `fig_daily_spread_change_bat{2,4,8}.jpg` | figures | `tukedip_pdflatex_utf-8/figures/` |
| `fig_daily_spread_change_eu_bat{2,4,8}.jpg` | figures | same |
| `fig_capture_rates_line.jpg` | figure | same |
| `fig_lcos_de00.jpg` | figure | same |
| `fig_lcos_mega.jpg` | figure | same |
| `fig_lcos_zoom.jpg` | figure | same |
| `fig_de00_daily_profile_curves.jpg` | figure | same |
| `fig_de00_daily_profile_areas.jpg` | figure | same |

After both notebooks have run, rebuild the thesis PDF:

```bash
cd tukedip_pdflatex_utf-8
pdflatex tukedip.tex && bibtex tukedip && pdflatex tukedip.tex && pdflatex tukedip.tex
```

---

## 7. Extending the analysis

### 7.1 Adding new values to an existing parameter

All parameter grids are defined at the top of
`scenarios/run_scenarios.py` (around line 70, inside dedicated `CORE_*`
lists). For example, to add a `bat_scale = 0.5` point (scenarios where
battery capacity is half of the TYNDP reference), edit:

```python
CORE_BAT_SCALES = [1, 2, 4, 8]
# to
CORE_BAT_SCALES = [0.5, 1, 2, 4, 8]
```

Then re-run steps 2–4 of the pipeline. `run_scenarios.py` automatically
skips scenarios that already have a network file, so only the 108 new
scenarios (`0.5× × 4 durations × 3 gas × 3 co2 × 3 CY`) will be built.
`solve_scenarios.py` will likewise only solve the new ones.

### 7.2 Adding a new parameter dimension

Same file. Extend the `_assemble_scenarios()` function (around line 276)
to include the new parameter in the product that generates scenario
tags. Make sure the new parameter is also forwarded to `build_network()`
inside `_build_one()`. Re-run the pipeline.

The existing notebooks will continue to work for the original grid but
will not automatically plot the new dimension — expect to add new cells
that filter on the new parameter.

### 7.3 Re-running affected steps

Only the steps after your change need to be re-run:

- Change in raw data → Steps 1–4.
- Change in `build_network.py` / `run_scenarios.py` parameters → Steps 2–4.
- Change in analysis only → Step 4 (if preprocess logic changed) or just
  re-run the notebook.

---

## 8. Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `gurobipy.GurobiError: No Gurobi license found` | `gurobi.lic` missing or `GRB_LICENSE_FILE` wrong | redo section 4, then `gurobi_cl --license` |
| `MemoryError` during solve | not enough RAM (~28 GB per worker) | reduce `--workers` to 1, or move to a larger server |
| `FileNotFoundError: lmp.parquet` from a notebook | Step 4 did not run or wrote to a different directory | run `preprocess_networks.py` pointing at your `solved_networks_core/` |
| `ModuleNotFoundError: plot_utils` | Jupyter kernel running from the wrong environment | *Kernel → Change Kernel* and pick the venv that has `plot_utils.py` on its path |
| `ImportError: cannot import name 'build_network'` | missing `grid-model/` helpers | extract `data/open-tyndp.tar.gz` and ensure `grid-model/helpers.py` is present |
| Figures in thesis PDF look stale | notebooks wrote to wrong folder | verify `save_fig` target is `tukedip_pdflatex_utf-8/figures/` in `scenario_analysis/plot_utils.py` |
