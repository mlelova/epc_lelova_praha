from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scenarios.load_network_data import load_network_data
from scenarios.run_scenarios import _build_one

data = load_network_data(
    data_dir="data/open-tyndp",
    tyndp_dir="data/tyndp2024",
    climate_year=2009
)

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
)

de_price = n.buses_t.marginal_price["DE00"]

print("Hours:", len(de_price))
print("Mean:", de_price.mean())
print("Median:", de_price.median())
print("Min:", de_price.min())
print("Max:", de_price.max())
print("Std:", de_price.std())
print("Hours >= 2999:", (de_price >= 2999).sum())