


# running the first baseline



After loading data files into parquets for easier processing, we loaded network

```text
preprocess_xlsx.py          ✓ DONE
        ↓
load_network_data.py        ← NEXT
        ↓
build_network.py            ← THEN
        ↓
run_scenarios.py
        ↓
solve_scenarios.py
```


load_network_data() returns a dictionary of all data required for a climate year

`from scenarios.load_network_data import load_network_data  
data = load_network_data(
    data_dir="data/open-tyndp",
    tyndp_dir="data/tyndp2024",
    climate_year=2009,
)  
print(data.keys())`

2009 was used to validate agianst the TYNDP results

### now we build the network
`
from scenarios.build_network import build_network
n = build_network(
    data,
    battery_scale=1.0,
    output_path="baseline_2030_cy2009.nc",
)`


creates file baseline_2030_cy2009.nc

Snapshots: 8760
Bidding zones / buses: 55 

55-bidding-zone European model with hourly resolution for the full yea

### solving the network: 

`status, condition = n.optimize(
    solver_name="gurobi",
    solver_options={
        "Threads": 3,
    }
)`

~ 4 minutes

check
print(status, condition)
should be
ok optimal

basic stats:

` print("Hours:", len(de_price))`


Hours: 8760

` print("Mean:", de_price.mean(`))
> 
Mean: 75.78198202509246

 print("Median:", de_price.medin
`))


Median: 79.8461151724
>> print("Min:", de_pic

mi`n(

): 0.0106 EUR/MWh
`06
>>> print("Made_`

pric`e.

max())
M000000000005 EUR/MWh`
`
00005
>>> p Std:",`

 de_`

price.43.32466598976225 EUR/MWh

df())
Std: 1
## irst runfor baseli66598976225`

we are off f and thesis' 61 eur/mwhrom the TYND
# run one scenario attempt instead of build network
 from pathlib import Path
from scenarios.load_network_data import load_network_data
from scenarios.run_scenarios import _build_one`
twe are assuming the network is built correctly
 data = load_network_data(
    data_dir="data/open-tyndp",
    tyndp_dir="data/tyndp2024",
    climate_year=2009
)`

e are choosing exactly one scenario that should be the og baseline
n = _build_one(
    base_data=data,
    gas_price=22.68,
    co2_price=113.4,
    battery_scale=1.0,
    load_scale=1.0,
    ntc_scale=1.0,
    battery_duration=1.0,
    nuclear_scale=1.0,
    battery_extendable=False,
    out_path=Path("thesis_baseline_cy2009.nc")
)`
`w
`
`
#P's 65.7 eur/mwh