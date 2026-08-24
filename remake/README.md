# Single-run company-data forecast CLI

`remake` is a strict company-data override layer around the trusted builders in
`scenarios/`. It always builds one network, optionally solves it with Gurobi,
and writes reproducibility metadata.

Run commands from the project root after installing `requirements.txt`.
Python 3.10 or newer is required. The available commands are:

```text
python -m remake run ...
python -m remake extract-capacities ...
python -m remake compare ...
```

For compatibility, run options may also be passed directly as
`python -m remake --tag ...`; this is equivalent to `python -m remake run`.

## Extract company installed capacities

Convert the monthly Germany company export into forecast-ready override files:

```bash
python -m remake extract-capacities \
  --source company_data/ins_cap.csv \
  --year 2030 \
  --bus DE00 \
  --output-dir company_data/processed
```

The command validates the semicolon-delimited, decimal-comma source and writes:

```text
company_data/processed/
  capacity_override_de00_2030.csv
  battery_override_de00_2030.csv
  capacity_override_de00_2030.audit.json
```

The static 2030 capacities are hour-weighted means of the twelve monthly
values: each first-of-month value is assumed to apply throughout that month.
The audit records the source hash and metadata, month weights, annual source
means, source-to-model mappings, baseline split proportions, generated values,
and changes from the TYNDP base.

Direct categories map to their corresponding DE00 carriers. Aggregate solar,
conventional gas, and pumped-hydro values are split using existing DE00 model
proportions. Hydro pump power and reservoir energy are scaled with turbine
power to preserve baseline ratios. Biomass, geothermal, and waste are combined
as `other-res`; generic `wnd` must remain zero to avoid double counting the
separate onshore and offshore series. Battery power retains the base DE00
two-hour duration because the company export contains GW but no GWh field.

Use the extracted values in a forecast:

```bash
python -m remake \
  --tag company_capacities_2030 \
  --climate-year 2009 \
  --capacity-override company_data/processed/capacity_override_de00_2030.csv \
  --battery-override company_data/processed/battery_override_de00_2030.csv \
  --build-only
```

The same invocation can be written explicitly as `python -m remake run ...`.

The current importer intentionally produces one static annual capacity set;
monthly time-varying installed capacity is not applied to the PyPSA network.

## Run a forecast

From the project root:

```bash
python -m remake \
  --tag company_forecast_cy2009 \
  --climate-year 2009 \
  --gas-price 31.2 \
  --coal-price 12.8 \
  --co2-price 113.4 \
  --battery-scale 1.0 \
  --battery-override company_data/batteries.csv \
  --capacity-override company_data/capacities.csv \
  --solve \
  --threads 2
```

Building without solving is the default. `--build-only` can be supplied to
make that choice explicit. Raw TYNDP inputs default to `data/tyndp2024`; use
`--tyndp-dir` if the downloaded dataset is elsewhere. Prepared model tables
default to `data/open-tyndp`; change that location with `--data-dir`.

The run-level controls are:

- `--gas-price`, `--coal-price`, and `--co2-price` override the corresponding
  technology assumptions.
- `--battery-scale`, `--ntc-scale`, and `--load-scale` multiply the base
  battery power, interconnector capacity, and demand respectively.
- `--battery-extendable` allows battery power capacity to be optimized.
- `--slack-cost` sets the slack-generator marginal cost in EUR/MWh (default
  `3000`).
- `--threads` controls Gurobi threads when `--solve` is used (default `2`).
- `--output-dir` changes the output root.

CSV overrides are supplied with `--capacity-override`,
`--technology-override`, `--battery-override`, `--ntc-override`,
`--nuclear-profile-override`, `--demand-override`, and `--vre-override`.

Outputs are grouped under `remake/output/` by default:

```text
built/<tag>.nc
solved/<tag>.nc        # only with --solve
runs/<tag>.json
```

The JSON records all CLI inputs as absolute paths, the Git commit and dirty
state, timestamps, output paths, solver result, objective, slack MWh, and any
error. The metadata file is also updated after a failed run.

## Override schemas and units

Unknown identifiers, duplicate keys, negative capacities, invalid
efficiencies, incomplete hourly profiles, and out-of-range capacity factors
are errors. VRE data must also contain a non-zero profile for every bus with
positive capacity in the corresponding wind or solar carrier. Input units are
encoded in the column names where possible.

### Capacity

Updates existing rows only. Use MW and MWh.

```csv
bus,index_carrier,p_nom_mw,e_nom_mwh
DE00,gas-ccgt,18000,0
```

`p_nom` and `e_nom` are accepted as aliases for compatibility with the base
scenario table, but retain the same MW/MWh units. Pumped-hydro pump capacity
is still supplied as positive MW; the override layer applies the engine's
internal negative charging-direction convention.

### Technology

Use exactly one key: `index_carrier` or `pypsa_carrier`. Any remaining columns
must already exist in `technologies_2030.csv`. Fuel prices are EUR/MWh thermal,
VOM and marginal costs are EUR/MWh electrical, emissions are tCO2/MWh, and
efficiency must be in `(0, 1]`.

```csv
index_carrier,efficiency,vom_eur_mwh,fuel_price_eur_mwh,marginal_cost_eur_mwh,co2_tco2_mwh
gas-ccgt,0.59,2.1,31.2,79.5,0.2
```

### Battery

```csv
bus,p_nom_mw,duration_h
DE00,5000,4
```

The bus must already have a modeled battery entry because the scenario engine
treats this input as an override, not as a new asset declaration.

### Interconnector NTC

```csv
link_id,p_nom
DE00-FR00-DC,5000
```

`p_nom` is MW and takes precedence over `--ntc-scale` for listed links.

### Nuclear profile

Supply exactly 8760 contiguous hourly rows. Wide form uses one bus per column:

```csv
snapshot,BE00,DE00
2030-01-01 00:00:00,0.90,0.85
2030-01-01 01:00:00,0.90,0.85
```

Long form (`snapshot,bus,p_max_pu`) is also accepted. Values must be in
`[0, 1]`. Listed bus columns replace the base profile; unlisted buses retain
their base profile.

### Demand profile

Demand overrides contain exactly 8760 contiguous hourly timestamps. They use
the same wide form as the nuclear profile or the long form
`snapshot,bus,demand_mw`. Demand values are MW and must be non-negative.

### VRE profiles

VRE overrides use long form with
`snapshot,technology,bus,p_max_pu`, where `technology` is one of
`wind_onshore`, `wind_offshore`, `solar_utility`, or `solar_rooftop`.
Each supplied technology/bus series must have exactly 8760 contiguous hourly
values in `[0, 1]`. Technologies not supplied retain their base profiles.
After all overrides are applied, each wind and solar carrier must have a
non-empty, non-zero profile for every bus where that carrier has positive
capacity.

## Compare a solved run with actual prices

```bash
python -m remake compare \
  --solved remake/output/solved/company_forecast_cy2009.nc \
  --actual company_data/actual_prices.csv \
  --zone DE00
```

Actual prices may be wide (`timestamp,DE00`), a single-zone file
(`timestamp,price_eur_mwh`), or long form
(`timestamp,zone,price_eur_mwh`). The command writes an aligned hourly CSV and
prints observation count, MAE, RMSE, MAPE (excluding zero actuals), mean bias,
and correlation. If `--output` is omitted, the CSV is written next to the
solved network as `<solved-stem>_comparison_<zone>.csv`.

This price comparison is separate from the interactive two-network dashboard
documented in [`visualisation/README.md`](../visualisation/README.md).
