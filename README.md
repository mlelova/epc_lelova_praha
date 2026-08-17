# Modelling the impact of battery storage on electricity prices in Europe

Master's thesis, Faculty of Electrical Engineering and Informatics, Technical University of Košice (FEI TUKE), 2026.

A full-scale dispatch model of the European electricity system for 2030
(TYNDP 2024 NT scenario, 55 bidding zones, 8760 hourly timesteps) built
with [PyPSA](https://pypsa.readthedocs.io/). The thesis explores how
battery storage capacity and discharge duration reshape locational
marginal prices, price volatility, technology capture rates and
arbitrage economics across a factorial matrix of 432 scenarios
(`bat_scale` × `bat_duration` × `gas_price` × `co2_price` ×
`climate_year`).

## Project structure

| Part | Folder | Purpose |
|---|---|---|
| **1. Scenario generation** | [`scenarios/`](scenarios/) | Build and solve the 432 PyPSA networks |
| **2. Scenario analysis** | [`scenario_analysis/`](scenario_analysis/) | Reproduce thesis figures and tables from solved data |
| **3. Grid-model reference** | [`grid-model/`](grid-model/), [`build_grid_model.ipynb`](build_grid_model.ipynb) | Jupyter walkthrough of how the base network and derived datasets were built (reference only — outputs are shipped with the repo) |

Parts 1 and 2 are the operational pipeline. Part 3 is documentation for
readers curious about the data-preparation stage; it is **not** required
to re-run the analysis.

## Where to start

- **Want to reproduce results (quick command list)?** → [`docs/SIMPLE_USER_GUIDE.md`](docs/SIMPLE_USER_GUIDE.md)
- **Want the detailed user guide (full context, flags, troubleshooting)?** → [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)
- **Want the short script reference (main function, inputs, outputs)?** → [`docs/SIMPLE_SYSTEM_GUIDE.md`](docs/SIMPLE_SYSTEM_GUIDE.md)
- **Want the detailed system guide (data flow, module layout)?** → [`docs/SYSTEM_GUIDE.md`](docs/SYSTEM_GUIDE.md)
- **Want to read the thesis PDF?** → [`tukedip_pdflatex_utf-8/tukedip.pdf`](tukedip_pdflatex_utf-8/tukedip.pdf)

## Quick start (shortcut path, ~10 minutes once data is downloaded)

1. Clone this repository.
2. Download the pre-solved data archive from `<LINK-TBD>` and extract
   `solved_networks_core/` + `data/tyndp2024/preprocessed/solved/` into
   the project root.
3. Create a Python environment and install requirements:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
4. Run the two analysis notebooks — from the terminal:
   ```bash
   jupyter execute scenario_analysis/sensitivity_analysis.ipynb
   jupyter execute scenario_analysis/peak_offpeak_analysis.ipynb
   ```
   …or interactively in the browser:
   ```bash
   jupyter lab
   ```
   (opens `scenario_analysis/sensitivity_analysis.ipynb` and
   `scenario_analysis/peak_offpeak_analysis.ipynb`; *Kernel → Restart
   Kernel and Run All Cells* in each).

For full replication from raw TYNDP data (data download, model build,
~30-hour Gurobi solve) follow [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md).


