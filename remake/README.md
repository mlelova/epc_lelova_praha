# Single-run forecast CLI

`remake` is a strict company-data override layer around the trusted builders in
`scenarios/`. It always builds one network, optionally solves it with Gurobi,
and writes reproducibility metadata.

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
`--tyndp-dir` if the downloaded dataset is elsewhere.

Outputs are grouped under `remake/output/` by default:

```text
built/<tag>.nc
solved/<tag>.nc
runs/<tag>.json
```

The JSON records all CLI inputs as absolute paths, the Git commit and dirty
state, timestamps, output paths, solver result, objective, slack MWh, and any
error. The metadata file is also updated after a failed run.

## Override schemas and units

Unknown identifiers, duplicate keys, negative capacities, invalid
efficiencies, incomplete hourly profiles, and out-of-range capacity factors
are errors. Input units are encoded in the column names where possible.

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

Demand overrides use the same wide form or long form
`snapshot,bus,demand_mw`. VRE overrides are long form with
`snapshot,technology,bus,p_max_pu`, where technology is one of
`wind_onshore`, `wind_offshore`, `solar_utility`, or `solar_rooftop`.

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
and correlation.
