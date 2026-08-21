from pathlib import Path

import matplotlib.pyplot as plt
import pypsa


# ============================================================
# 1. Path to solved PyPSA network
# ============================================================

NETWORK_PATH = Path(
    "solved_networks_core/bat1x_gas22.68_co2_113.4_cy2009_dur1x.nc"
)


# ============================================================
# 2. Load solved network
# ============================================================

print(f"Loading network: {NETWORK_PATH}")

n = pypsa.Network(NETWORK_PATH)

print("Network loaded.")
print(f"Snapshots: {len(n.snapshots)}")


# ============================================================
# 3. Extract hourly DE00 marginal prices
# ============================================================

if "DE00" not in n.buses.index:
    raise KeyError(
        f"DE00 not found in network buses.\n"
        f"Available buses: {n.buses.index.tolist()}"
    )

de_price = n.buses_t.marginal_price["DE00"]

print("\nDE00 price statistics:")
print(f"Mean:   {de_price.mean():.2f} EUR/MWh")
print(f"Median: {de_price.median():.2f} EUR/MWh")
print(f"Min:    {de_price.min():.2f} EUR/MWh")
print(f"Max:    {de_price.max():.2f} EUR/MWh")


# ============================================================
# 4. Plot full year of hourly prices
# ============================================================

fig, ax = plt.subplots(figsize=(15, 5))

ax.plot(
    de_price.index,
    de_price,
    linewidth=0.7,
    label="Hourly DE00 price",
)

ax.axhline(
    de_price.mean(),
    linestyle="--",
    linewidth=1.5,
    label=f"Annual mean = {de_price.mean():.2f} EUR/MWh",
)

ax.set_title("DE00 Hourly Electricity Price")
ax.set_xlabel("Date")
ax.set_ylabel("Marginal price [EUR/MWh]")

ax.legend()
ax.grid(alpha=0.25)

plt.tight_layout()
plt.show()