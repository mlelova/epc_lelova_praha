# Solved-network dashboards

`visualisation` converts solved PyPSA NetCDF (`.nc`) networks into standalone,
interactive HTML reports. The generated file embeds its data and styling, so
it can be opened directly in a modern browser without a web server or internet
connection.

Run commands from the project root after installing `requirements.txt`.
Python 3.10 or newer is required.

## Create a dashboard for one network

```bash
python -m visualisation.cli \
  remake/output/solved/company_forecast_cy2009.nc
```

By default this writes
`visualisation/output/<input-stem>_dashboard.html`. To select the output,
initial scope, and title:

```bash
python -m visualisation.cli solved_network.nc \
  --output visualisation/output/company_forecast.html \
  --default-zone DE00 \
  --title "2030 company forecast"
```

Every network bus is selectable. An additional `EUROPE` scope aggregates all
modeled zones; `DE00` is initially selected when present, otherwise the first
bus is used. Select the aggregate initially with `--default-zone EUROPE`.

The report includes:

- price levels, duration curves, monthly structure, volatility, negative
  hours, and TB1/TB2/TB4 spreads;
- demand, residual load, and the price/residual-load relationship;
- battery dispatch, state of charge, equivalent cycles, and gross wholesale
  dispatch margin;
- installed capacity and weighted generation by carrier; and
- zonal imports and exports, or internal transfers and losses for `EUROPE`.

Battery gross margin excludes capital cost, fixed O&M, degradation, grid fees,
and ancillary-market revenue. Dashboard values come from the solved network;
an input without bus marginal prices is rejected as unsolved.

## Compare two solved networks

```bash
python -m visualisation.comparison_cli \
  remake/output/solved/current.nc \
  remake/output/solved/baseline.nc \
  --output visualisation/output/current_vs_baseline.html \
  --default-zone DE00 \
  --current-label "Latest calibration" \
  --baseline-label "Old baseline" \
  --title "Calibration comparison"
```

When `--output` is omitted, the comparison is written to
`visualisation/output/network_comparison_dashboard.html`.

The inputs must have identical snapshots in the same order and identical
zones in the same order. The dashboard compares system objective, prices,
demand, generation, renewable share, batteries, carrier capacities and
generation, and cross-border flows. It reports current and baseline values
with absolute and percentage changes; percentage change is omitted when the
baseline is zero.

## Python API

```python
from visualisation import generate_comparison_dashboard, generate_dashboard

dashboard = generate_dashboard(
    "solved_network.nc",
    output_path="visualisation/output/network.html",
    default_zone="EUROPE",
)

comparison = generate_comparison_dashboard(
    "current.nc",
    "baseline.nc",
    output_path="visualisation/output/comparison.html",
    current_label="Current",
    baseline_label="Baseline",
)
```

Both functions return the absolute `pathlib.Path` of the generated HTML file.
Input files must end in `.nc`, and output files must end in `.html`.

## Files and tests

- `visualisation.py` and `dashboard_template.html` implement the single-model
  report.
- `comparison.py` and `comparison_dashboard_template.html` implement the
  two-model report.
- `cli.py` and `comparison_cli.py` expose the command-line interfaces.

Run the dashboard tests with:

```bash
python -m unittest tests.test_visualisation tests.test_visualisation_comparison
```
