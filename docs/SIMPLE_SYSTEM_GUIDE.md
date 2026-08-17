# Simple System Guide

Facts only: each pipeline script and analysis notebook, its main entry
point, inputs, outputs.

---

## `scenarios/preprocess_xlsx.py`

- **Main idea:** one-time conversion of slow XLSX and PECD CSV sources to
  Parquet.
- **Entry point:** `python scenarios/preprocess_xlsx.py` (no CLI flags).
- **Inputs:**
    - `data/tyndp2024/Demand Profiles/`
    - `data/tyndp2024/Hydro Inflows/`
    - `data/tyndp2024/PECD 2030/`
- **Outputs** — written to `data/tyndp2024/preprocessed/`:
    - `demand_profiles.parquet`
    - `hydro_inflows.parquet`
    - `pecd_wind_onshore.parquet`
    - `pecd_wind_offshore.parquet`
    - `pecd_solar_utility.parquet`
    - `pecd_solar_rooftop.parquet`
    - `pecd_solar_generic.parquet`

---

## `scenarios/load_network_data.py`

- **Main idea:** read every dataset needed to build one network into a
  single dict.
- **Main function:** `load_network_data(data_dir, tyndp_dir, climate_year, gas_price=None, co2_price=None) -> dict`
- **Inputs:**
    - `data_dir` — derived datasets folder (default `data/open-tyndp`)
    - `tyndp_dir` — raw TYNDP folder (default `data/tyndp2024`)
    - `climate_year` — e.g. `2009`
    - `gas_price`, `co2_price` — optional overrides
- **Outputs:** a dict with keys
  `buses`, `generators`, `storage_units`, `links`, `loads`, `carriers`,
  `wind_onshore`, `wind_offshore`, `solar_utility`, `solar_rooftop`,
  `solar_generic`, `demand`, `hydro_inflow`, `hydro_ror`, `costs`.

---

## `scenarios/build_network.py`

- **Main idea:** turn the dict from `load_network_data` into an unsolved
  PyPSA network.
- **Main function:** `build_network(data, ntc_scale=1.0, load_scale=1.0, battery_scale=1.0, battery_override=None, nuclear_p_max_pu=None, ntc_override=None, battery_extendable=False, slack_cost=3_000.0, output_path=None) -> pypsa.Network`
- **Inputs:**
    - `data` — output of `load_network_data`
    - `ntc_scale`, `load_scale`, `battery_scale` — scalar multipliers
    - `battery_override`, `nuclear_p_max_pu`, `ntc_override` — optional
      per-zone / per-link overrides
    - `battery_extendable`, `slack_cost`, `output_path`
- **Outputs:**
    - returns `pypsa.Network`
    - if `output_path` is set, writes a `.nc` file there

---

## `scenarios/run_scenarios.py`

- **Main idea:** enumerate the scenario matrix, call
  `load_network_data` + `build_network` for each combination, save
  unsolved `.nc` files.
- **Entry point:** `python scenarios/run_scenarios.py`
- **CLI flags:**
    - `--matrix {core,nuclear,demand,ntc,invest}` — which scenario matrix to build (`core` = 432 thesis scenarios; alternate matrices are optional experiments)
    - `--workers N` — parallel worker processes (default `4`)
    - `--remove-old` — wipe `networks_<matrix>/` before building
    - `--dry-run` — print plan and write manifest, do not build
- **Inputs:** parameter grid hard-coded in the script; raw + derived data
  from `data/`.
- **Outputs** — written to `scenarios/networks_<matrix>/`:
    - `<tag>.nc` (one file per scenario)
    - `manifest.csv`

---

## `scenarios/solve_scenarios.py`

- **Main idea:** solve every unsolved network with Gurobi in parallel.
- **Entry point:** `python scenarios/solve_scenarios.py`
- **CLI flags:**
    - `--networks-dir PATH` — folder with unsolved `.nc` + `manifest.csv` (default `scenarios/networks_core`)
    - `--output-dir PATH` — folder for solved `.nc` (default `solved_networks`; the thesis uses `solved_networks_core`)
    - `--workers N` — parallel Gurobi processes (default `1`; each uses ~28 GB RAM)
    - `--threads N` — Gurobi threads per worker (default `2`)
    - `--tag TAG` — solve only a single named scenario (useful for debugging)
    - `--force` — re-solve even if the output file already exists
- **Inputs:** unsolved `.nc` files + `manifest.csv` from
  `--networks-dir`.
- **Outputs** — written to `--output-dir`:
    - `<tag>.nc` (one solved network per scenario)
    - `<tag>_gurobi.log`
    - `solve_results.csv` (status, solve time, slack MWh per scenario)

---

## `scenarios/preprocess_networks.py`

- **Main idea:** convert solved `.nc` files into Parquet tables for the
  analysis notebooks.
- **Entry point:** `python scenarios/preprocess_networks.py`
- **Main function:** `main(solved_dir, out_dir) -> None`
- **CLI flags:**
    - `--solved-dir PATH` — folder with solved `.nc` files (default `solved_networks_core`)
    - `--out-dir PATH` — target folder for Parquet outputs (default `data/tyndp2024/preprocessed/solved/`)
- **Inputs:** solved `.nc` files from `--solved-dir`.
- **Outputs** — written to `--out-dir`
  (default `data/tyndp2024/preprocessed/solved/`):
    - `lmp.parquet`
    - `gen_p.parquet`
    - `storage_p.parquet`
    - `links_p0.parquet`
    - `static_generators.parquet`
    - `static_storage_units.parquet`
    - `static_links.parquet`

---

## `scenario_analysis/sensitivity_analysis.ipynb`

- **Main idea:** produces the figures and printed tables for thesis
  chapters 5 (volatility) and 6 (mechanism).
- **Inputs:** Parquet files from `data/tyndp2024/preprocessed/solved/`
  plus `solved_networks_core/solve_results.csv`.
- **Outputs:** figures written to `tukedip_pdflatex_utf-8/figures/`, and
  printed tables (`t:anova_summary`, `t:ols_coefs`, `t:spillover`) shown
  inside the notebook cells.
  Open the notebook after running it to inspect every output — the run
  command is in [`SIMPLE_USER_GUIDE.md`](SIMPLE_USER_GUIDE.md).

---

## `scenario_analysis/peak_offpeak_analysis.ipynb`

- **Main idea:** produces the figures for thesis chapters 7 (price
  structure) and 8 (battery economics).
- **Inputs:** Parquet files from `data/tyndp2024/preprocessed/solved/`
  plus `solved_networks_core/solve_results.csv`.
- **Outputs:** figures written to `tukedip_pdflatex_utf-8/figures/`.
  Open the notebook after running it to inspect every output — the run
  command is in [`SIMPLE_USER_GUIDE.md`](SIMPLE_USER_GUIDE.md).
