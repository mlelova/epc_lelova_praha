
After loading data files into parquets for easier processing, we loaded network

preprocess_xlsx.py          ✓ DONE
        ↓
load_network_data.py        ← NEXT
        ↓
build_network.py            ← THEN
        ↓
run_scenarios.py
        ↓
solve_scenarios.py

load_network_data() returns a dictionary of all data required for a climate year

from scenarios.load_network_data import load_network_data

data = load_network_data(
    data_dir="data/open-tyndp",
    tyndp_dir="data/tyndp2024",
    climate_year=2009,
)

print(data.keys())

2009 was used to validate agianst the TYNDP results

now we build the network

from scenarios.build_network import build_network

n = build_network(
    data,
    battery_scale=1.0,
    output_path="baseline_2030_cy2009.nc",
)

creates file baseline_2030_cy2009.nc

Snapshots: 8760
Bidding zones / buses: 55 

55-bidding-zone European model with hourly resolution for the full yea

solving the network: 

status, condition = n.optimize(
    solver_name="gurobi",
    solver_options={
        "Threads": 3,
    }
)

~ 4 minutes

check
print(status, condition)
should be
ok optimal

basic stats:

>>> print("Hours:", len(de_price))
Hours: 8760
>>> print("Mean:", de_price.mean())
Mean: 75.78198202509246
>>> print("Median:", de_price.median())
Median: 79.84611517241379
>>> print("Min:", de_price.min())
Min: 0.0106
>>> print("Max:", de_price.max())
Max: 3000.0000000000005
>>> print("Std:", de_price.std())
Std: 143.32466598976225

we are off from the TYNDP's 65.7 eur/mwh