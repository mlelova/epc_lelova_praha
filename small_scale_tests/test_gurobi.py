import pypsa
import pandas as pd

print("PyPSA version:", pypsa.__version__)

# Create network
n = pypsa.Network()

# 24 hourly snapshots
snapshots = pd.date_range(
    "2026-01-01",
    periods=24,
    freq="h"
)
n.set_snapshots(snapshots)

# One electricity bus
n.add("Bus", "DE")

# Cheap generator: 50 MW
n.add(
    "Generator",
    "cheap_generator",
    bus="DE",
    p_nom=50,
    marginal_cost=20,
)

# Expensive generator: 100 MW
n.add(
    "Generator",
    "expensive_generator",
    bus="DE",
    p_nom=100,
    marginal_cost=80,
)

# Constant demand: 70 MW
n.add(
    "Load",
    "demand",
    bus="DE",
    p_set=70,
)

print("\nSolving with Gurobi...")

status, condition = n.optimize(
    solver_name="gurobi"
)

print("\nStatus:", status)
print("Condition:", condition)

print("\nGenerator dispatch:")
print(n.generators_t.p.head())

print("\nElectricity price:")
print(n.buses_t.marginal_price.head())