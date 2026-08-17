# System Guide

A reference for every file in the operational pipeline. For each script
the main idea, the main entry point with its inputs and outputs, and the
command-line interface (when applicable) are listed in the same format.

This guide assumes the project layout described in
[`../README.md`](../README.md) and is complementary to
[`USER_GUIDE.md`](USER_GUIDE.md), which focuses on *running* the project
rather than understanding it.

---

## 1. Overview & data flow

```
                       raw XLSX / PECD CSV
                                │
                                ▼
                 ┌────────────────────────────┐
                 │ scenarios/preprocess_xlsx  │  (Step 1, one-time)
                 └────────────────────────────┘
                                │
                                ▼
                  data/tyndp2024/preprocessed/*.parquet
                                │
                                ▼
                 ┌────────────────────────────┐
                 │ scenarios/load_network_data│  (module — called by Step 2)
                 └────────────────────────────┘
                                │
                                ▼
                           dict of DataFrames
                                │
                                ▼
                 ┌────────────────────────────┐
                 │ scenarios/build_network    │  (module — called by Step 2)
                 └────────────────────────────┘
                                │
                                ▼
                 ┌────────────────────────────┐
                 │ scenarios/run_scenarios    │  (Step 2)
                 └────────────────────────────┘
                                │
                                ▼
              scenarios/networks_core/*.nc + manifest.csv
                                │
                                ▼
                 ┌────────────────────────────┐
                 │ scenarios/solve_scenarios  │  (Step 3 — Gurobi)
                 └────────────────────────────┘
                                │
                                ▼
         solved_networks_core/*.nc + solve_results.csv
                                │
                                ▼
                 ┌────────────────────────────┐
                 │ scenarios/preprocess_nets  │  (Step 4)
                 └────────────────────────────┘
                                │
                                ▼
            data/tyndp2024/preprocessed/solved/*.parquet
                                │
                                ▼
                 ┌────────────────────────────┐
                 │ scenario_analysis/*.ipynb  │  (analysis)
                 └────────────────────────────┘
                                │
                                ▼
                tukedip_pdflatex_utf-8/figures/*.jpg
```

**File formats.** PyPSA networks use NetCDF (`.nc`) for round-tripping
full network state. All data consumed by the analysis notebooks uses
Parquet (`.parquet`) because column-wise reads are orders of magnitude
faster than reopening every `.nc` scenario.

---

## 2. `scenarios/` — pipeline scripts

### `preprocess_xlsx.py`

- **Main idea:** one-time conversion of slow XLSX and PECD CSV sources
  to fast Parquet, so every subsequent network build reads pre-parsed
  columns instead of re-parsing XML.
- **Entry point:** `python scenarios/preprocess_xlsx.py` (runs all three
  sub-steps sequentially).
- **Sub-functions:**
    - `preprocess_demand()` — TYNDP demand XLSX → `demand_profiles.parquet`
    - `preprocess_hydro()` — hydro inflow XLSX → `hydro_inflows.parquet`
    - `preprocess_pecd()` — 258 PECD CSVs → five per-technology parquet files
- **Inputs:**
    - `data/tyndp2024/Demand Profiles/` — XLSX per climate year
    - `data/tyndp2024/Hydro Inflows/` — XLSX per climate year
    - `data/tyndp2024/PECD 2030/` — hourly CF CSV per bus and technology
- **Outputs:**
    - `data/tyndp2024/preprocessed/demand_profiles.parquet`
    - `data/tyndp2024/preprocessed/hydro_inflows.parquet`
    - `data/tyndp2024/preprocessed/pecd_wind_onshore.parquet`
    - `data/tyndp2024/preprocessed/pecd_wind_offshore.parquet`
    - `data/tyndp2024/preprocessed/pecd_solar_utility.parquet`
    - `data/tyndp2024/preprocessed/pecd_solar_rooftop.parquet`
    - `data/tyndp2024/preprocessed/pecd_solar_generic.parquet`
- **CLI flags:** none — the script is either run or not.

### `load_network_data.py`

- **Main idea:** read every dataset needed to build one PyPSA network
  for a given climate year + gas/CO₂ price, and return it as a single
  dict.
- **Main function:** `load_network_data(data_dir, tyndp_dir, climate_year, gas_price=None, co2_price=None) -> dict`
- **Inputs:**
    - `data_dir: Path | str` — derived datasets directory (default `data/open-tyndp`)
    - `tyndp_dir: Path | str` — raw TYNDP directory (default `data/tyndp2024`)
    - `climate_year: int` — one of 1982–2019 (TYNDP coverage); the factorial matrix uses 2003, 2009, 2012
    - `gas_price: float | None` — overrides default from the scenario; `None` keeps the TYNDP default
    - `co2_price: float | None` — overrides default CO₂ price; `None` keeps the TYNDP default
- **Outputs (dict keys):**
    - `buses` — zone list and metadata
    - `generators`, `storage_units`, `links`, `loads`, `carriers` — static tables
    - `wind_onshore`, `wind_offshore`, `solar_*` — hourly capacity factors per bus
    - `demand` — hourly load per bus
    - `hydro_inflow`, `hydro_ror` — hourly hydro profiles
    - `costs` — technology cost/efficiency table (with gas/CO₂ overrides applied)
- **CLI flags:** none — this is a module, imported by `run_scenarios.py`.

### `build_network.py`

- **Main idea:** turn the dict from `load_network_data` into a fully
  populated, unsolved PyPSA network.
- **Main function:** `build_network(data, ntc_scale=1.0, load_scale=1.0, battery_scale=1.0, battery_override=None, nuclear_p_max_pu=None, ntc_override=None, battery_extendable=False, slack_cost=3_000.0, output_path=None) -> pypsa.Network`
- **Inputs:**
    - `data: dict` — output of `load_network_data`
    - `ntc_scale: float` — multiplier for every NTC (1.0 = reference)
    - `load_scale: float` — multiplier for hourly demand (1.0 = reference)
    - `battery_scale: float` — multiplier for battery power (the `bat_scale`
      parameter of the thesis matrix)
    - `battery_override: pd.DataFrame | None` — optional override table
      (per-zone p_nom / max_hours); used to change `bat_duration` without
      changing `battery_scale`
    - `nuclear_p_max_pu: pd.DataFrame | None` — optional override of nuclear
      availability profile (used by the `nuclear` matrix)
    - `ntc_override: pd.DataFrame | None` — optional per-link NTC override
      (used by the `ntc` matrix)
    - `battery_extendable: bool` — whether `p_nom` is a variable (for the
      `invest` matrix) or fixed (default, for all thesis scenarios)
    - `slack_cost: float` — EUR/MWh for the slack generator in every zone
      (3 000 = VoLL used by ENTSO-E)
    - `output_path: str | None` — optional `.nc` path to export the finished
      network; `None` returns the network in memory only
- **Outputs:**
    - returns the populated `pypsa.Network` object
    - optionally writes a NetCDF file to `output_path`
- **CLI flags:** none — this is a module. The file has a docstring
  example at the top demonstrating standalone use.

### `run_scenarios.py`

- **Main idea:** enumerate the factorial scenario matrix, call
  `load_network_data` + `build_network` for each combination, and save
  the resulting unsolved networks to disk as `.nc`.
- **Main function:** `run(matrix_filter, workers, remove_old, dry_run)`
  (invoked by the `__main__` block; normal use is the CLI).
- **Inputs:**
    - `--matrix {core,nuclear,demand,ntc,invest}` — build one of the five
      matrices; `core` is the 432-scenario thesis matrix (default: all)
    - `--workers N` — parallel worker processes (default 4; each worker
      re-uses a pre-loaded dataset for its assigned climate year)
    - `--remove-old` — delete the target `networks_<matrix>/` directory
      before building
    - `--dry-run` — print the scenario plan, write `manifest.csv`, but do
      not build any networks
- **Outputs:**
    - `scenarios/networks_<matrix>/<tag>.nc` — one unsolved PyPSA network
      per scenario; `<tag>` encodes all parameters
      (e.g. `bat1x_gas22.68_co2_113.4_cy2009_dur1x`)
    - `scenarios/networks_<matrix>/manifest.csv` — table of every scenario
      with its tag and parameter values
- **CLI flags:** all listed above.

### `solve_scenarios.py`

- **Main idea:** solve every unsolved network with Gurobi in parallel,
  write the solved `.nc` files and a per-run CSV manifest of solve
  statistics.
- **Main function:** `solve_network(network_path, output_path, threads) -> dict` (one network) — orchestrated by `run(...)` and the `__main__` block.
- **Inputs:**
    - `--networks-dir PATH` — directory containing unsolved `.nc` files +
      `manifest.csv` (default `scenarios/networks_core`)
    - `--output-dir PATH` — directory for solved `.nc` files (default
      `solved_networks`; the thesis uses `solved_networks_core`)
    - `--workers N` — parallel Gurobi processes (default 1; each uses
      ~28 GB RAM)
    - `--threads N` — Gurobi threads per worker (default 2)
    - `--tag TAG` — solve a single named scenario (handy for debugging)
    - `--force` — re-solve scenarios even if output files already exist
- **Outputs:**
    - `<output-dir>/<tag>.nc` — solved PyPSA network
    - `<output-dir>/<tag>_gurobi.log` — Gurobi log for that scenario
    - `<output-dir>/solve_results.csv` — running manifest: solve status,
      time, slack MWh, total cost per scenario (appended after each solve)
- **CLI flags:** all listed above.

### `preprocess_networks.py`

- **Main idea:** convert the 432 solved `.nc` files into column-oriented
  Parquet tables that the analysis notebooks can load in seconds.
- **Main function:** `main(solved_dir, out_dir) -> None`
- **Inputs:**
    - `--solved-dir PATH` — directory of solved `.nc` files (default
      `solved_networks_core`)
    - `--out-dir PATH` — target directory for Parquet outputs (default
      `data/tyndp2024/preprocessed/solved/`)
- **Outputs (Parquet files in `--out-dir`):**
    - `lmp.parquet` — hourly locational marginal prices, every tag × every zone
    - `gen_p.parquet` — hourly generator dispatch, every tag × every generator
    - `storage_p.parquet` — hourly storage unit power, every tag × every unit
    - `links_p0.parquet` — hourly link flows, every tag × every link
    - `static_generators.parquet` — static generator metadata (name, bus, carrier, `p_nom`, `max_hours`)
    - `static_storage_units.parquet` — static storage unit metadata
    - `static_links.parquet` — static link metadata (endpoints, NTC)
- **CLI flags:** both flags listed above.

---

## 3. `scenario_analysis/` — analysis notebooks

### `plot_utils.py`

- **Main idea:** shared thesis-style plotting helpers, so every figure
  coming out of the two notebooks has identical fonts, sizes, colours
  and file output paths.
- **Exports:**
    - `apply_thesis_style()` — set matplotlib rcParams (8 pt base, 9 pt
      titles, LaTeX-compatible sans-serif)
    - `thesis_subplots(layout, nrows=1, ncols=1, **kwargs)` — preset
      figure sizes keyed by layout name (`'single_short'`, `'single_tall'`,
      `'horizontal_2'`, `'horizontal_3'`, `'stacked_3'`, `'grid_3x3'`,
      `'full_page'`, …), all 6.0 inches wide to match the LaTeX textwidth
    - `save_fig(fig, name)` — save as JPG into
      `tukedip_pdflatex_utf-8/figures/fig_<name>.jpg` at thesis-ready DPI
    - `CARRIER_COLORS` — dict mapping carrier name → hex colour used in
      merit-order figures
    - `SCALE_COLORS`, `SCALE_MARKERS`, `SCALE_LABELS` — per-`bat_scale`
      colour, marker shape and display label
    - `CY_LABELS` — per-climate-year display label (e.g. `2009 → 'CY2009'`)
    - `PARAM_AXIS_LABELS`, `POSITIVE`, `NEGATIVE`, `NEUTRAL`, `REFLINE`
      — semantic colour constants for annotations and reference lines
- **Used by:** both analysis notebooks.

### `sensitivity_analysis.ipynb`

- **Main idea:** produces every thesis figure and table for chapters 5
  (volatility sensitivity) and 6 (mechanism of battery impact).
- **Inputs:**
    - `data/tyndp2024/preprocessed/solved/lmp.parquet`
    - `data/tyndp2024/preprocessed/solved/storage_p.parquet`
    - `data/tyndp2024/preprocessed/solved/static_generators.parquet`
    - `data/tyndp2024/preprocessed/solved/static_storage_units.parquet`
    - `data/tyndp2024/preprocessed/solved/static_links.parquet`
    - `data/tyndp2024/preprocessed/solved/links_p0.parquet`
    - `data/tyndp2024/preprocessed/solved/gen_p.parquet`
    - `solved_networks_core/solve_results.csv`
    - one solved `.nc` file (used to read demand weights)
- **Outputs (thesis figures and tables):**
    - Chapter 5:
        - table `t:anova_summary` (printed ANOVA η², F, p for DE00 and EU)
        - table `t:ols_coefs` (printed OLS coefficients)
        - `fig_spatial_cv.jpg`
    - Chapter 6:
        - `fig_merit_order_pct_bat{2,4,8}.jpg`
        - `fig_import_change_pct_bat{2,4,8}.jpg`
        - `fig_price_correlation.jpg`
        - table `t:spillover` (printed DE00 + DKE1 statistics)
        - `fig_eu_cv_vs_batscale.jpg`
- **Cell structure:** the notebook uses markdown headers in the form
  `## Chapter X` and `### Sub-topic` to separate logical blocks. Running
  cells in order is sufficient; each section's markdown header names the
  thesis artefact it produces.
- **Dependency chain:** the chapter-5 cells build the objects
  `res`, `res_vol`, `eu_weighted_cv` and `bus_cols_lmp` that chapter-6
  cells reuse. Running the notebook out of order will therefore fail.

### `peak_offpeak_analysis.ipynb`

- **Main idea:** produces every thesis figure for chapters 7 (price
  structure and capture rates) and 8 (battery economics — LCOS and
  arbitrage).
- **Inputs:**
    - `data/tyndp2024/preprocessed/solved/lmp.parquet`
    - `data/tyndp2024/preprocessed/solved/gen_p.parquet`
    - `data/tyndp2024/preprocessed/solved/storage_p.parquet`
    - `data/tyndp2024/preprocessed/solved/static_generators.parquet`
    - `data/tyndp2024/preprocessed/solved/static_storage_units.parquet`
    - `solved_networks_core/solve_results.csv`
- **Outputs (thesis figures):**
    - Chapter 7:
        - `fig_daily_spread_change_bat{2,4,8}.jpg`
        - `fig_daily_spread_change_eu_bat{2,4,8}.jpg`
        - `fig_capture_rates_line.jpg`
    - Chapter 8:
        - `fig_lcos_de00.jpg`
        - `fig_lcos_mega.jpg`
        - `fig_lcos_zoom.jpg`
        - `fig_de00_daily_profile_curves.jpg`
        - `fig_de00_daily_profile_areas.jpg`
- **Cell structure:** markdown sectioned by chapter and topic
  (`## Chapter 7 — Price structure`, `## Chapter 8 — Battery
  economics`, sub-sections for each figure group).
- **Dependency chain:** chapter-7 cells build `spread_df` and `cr_df`
  which are not required by chapter 8; chapter-8 cells build
  `lcos_all`, `zone_agg`, `compute_lcos` which are independent of
  chapter 7. Either chapter can be run on its own after the setup cell.

---

## 4. Data directory reference

| Path | Purpose | Source | Size |
|---|---|---|---|
| `data/tyndp2024/` | Raw ENTSO-E TYNDP publications (PECD 2030, PEMMDB2, demand, hydro, Plexos reference output) | External download (`<LINK-TBD>` mirror or ENTSO-E portal) | ~8.5 GB |
| `data/tyndp2024/preprocessed/` | Parquet converted inputs (demand, hydro, PECD) | Generated by `preprocess_xlsx.py` | ~1 GB |
| `data/tyndp2024/preprocessed/solved/` | Parquet tables consumed by notebooks | Generated by `preprocess_networks.py` | ~1 GB |
| `data/open-tyndp/` | Derived datasets used directly by `build_network.py` (bus list, cost tables, carriers, technology map) | Extracted archive shipped with the repo | ~1 GB |
| `solved_networks_core/` | 432 solved PyPSA networks + Gurobi logs + `solve_results.csv` | Generated by `solve_scenarios.py`, or downloaded as pre-solved archive | ~22 GB |

---

## 5. Output directory reference

| Path | Purpose |
|---|---|
| `tukedip_pdflatex_utf-8/figures/` | Thesis figures written by the two analysis notebooks (JPG, 6.0 inch width) |
| `tukedip_pdflatex_utf-8/*.tex` | LaTeX chapter files that reference the figures |
| `tukedip_pdflatex_utf-8/tukedip.pdf` | Compiled thesis document |

Figures are regenerated every time a notebook cell runs. A subsequent
`pdflatex` rebuild picks them up automatically via the
`\graphicspath{{figures/}}` declaration in `tukedip.tex`.
