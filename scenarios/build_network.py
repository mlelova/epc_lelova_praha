"""
build_network.py
================
Builds a PyPSA TYNDP 2030 electricity network from pre-loaded DataFrames and
returns an unsolved network ready for optimization.

All data loading is handled by load_network_data.py.  This module receives a
dict of DataFrames from load_network_data() and assembles the PyPSA network.

Usage
-----
    from load_network_data import load_network_data
    from build_network import build_network

    data = load_network_data(
        data_dir="data/open-tyndp",
        tyndp_dir="data/tyndp2024",
        climate_year=2009,
        gas_price=35.0,
        co2_price=85.0,
    )

    n = build_network(data, output_path="networks/baseline.nc")
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

sys.path.append(str(Path(__file__).resolve().parent.parent / "grid-model"))
from helpers import (  # noqa: E402
    STORAGE_CARRIERS,
    WIND_CARRIERS,
    SOLAR_CARRIERS,
    HYDRO_ROR_CARRIERS,
    CONVENTIONAL_CARRIERS,
)

warnings.filterwarnings("ignore")

SKIP_GENERATOR_CARRIERS = {"electrolyser", "h2"}

FALLBACK_CF = 0.85

DSR_NODE_MAP = {"UK00": "GB00", "UKNI": "GBNI"}


# ===========================================================================
# Internal helpers (pure builders — no I/O)
# ===========================================================================


def _reindex_to_2030(df: pd.DataFrame) -> pd.DataFrame:
    """Shift a time-indexed DataFrame to 2030 timestamps (keeps same length)."""
    df = df.copy()
    df.index = pd.date_range("2030-01-01", periods=len(df), freq="h")
    return df


def _build_topology(
    n: pypsa.Network,
    data: dict,
    ntc_scale: float,
    ntc_override: pd.DataFrame | None,
) -> None:
    """Add buses and DC links to the network."""
    for _, row in data["buses"].iterrows():
        n.add(
            "Bus",
            name=row["bus_id"],
            x=row["x"],
            y=row["y"],
            country=row["country"],
            carrier="AC",
            v_nom=row.get("voltage", 380),
        )

    ntc_lookup: dict[str, float] = {}
    if ntc_override is not None:
        for _, row in ntc_override.iterrows():
            ntc_lookup[str(row["link_id"])] = float(row["p_nom"])

    added = skipped = 0
    for link_id, row in data["links"].iterrows():
        bus0, bus1, p_nom = row["bus0"], row["bus1"], row["p_nom"]
        if p_nom <= 0:
            skipped += 1
            continue
        if bus0 not in n.buses.index or bus1 not in n.buses.index:
            skipped += 1
            continue

        final_p_nom = (
            ntc_lookup[str(link_id)]
            if str(link_id) in ntc_lookup
            else p_nom * ntc_scale
        )

        n.add(
            "Link",
            name=link_id,
            bus0=bus0,
            bus1=bus1,
            p_nom=final_p_nom,
            length=row["length"] / 1000,
            carrier="DC",
            efficiency=0.97,
            p_nom_extendable=False,
        )
        added += 1

    print(
        f"  Topology: {len(n.buses)} buses, {added} DC links "
        f"(skipped {skipped}, ntc_scale={ntc_scale})"
    )


def _add_carriers(n: pypsa.Network, data: dict) -> None:
    """Add all carriers with CO₂ emission factors."""
    n.add("Carrier", "AC", nice_name="AC Grid", color="#d4af37")
    n.add("Carrier", "DC", nice_name="DC Link", color="#a0a0a0")
    n.add(
        "Carrier",
        "dsr",
        nice_name="Demand Side Response",
        color="#f97316",
        co2_emissions=0.0,
    )

    CARRIER_COLORS = {
        "onwind": "#3B6182",
        "offwind": "#1a3a5c",
        "solar-pv-utility": "#FFDD00",
        "solar-pv-rooftop": "#FFE966",
        "solar-thermal": "#FFB300",
        "solar-thermal-w-storage": "#FF8C00",
        "battery": "#b8ea04",
        "electrolyser": "#ff29d9",
        "gas": "#d35050",
        "gas-ccs": "#ff9494",
        "coal": "#707070",
        "coal-ccs": "#b0b0b0",
        "lignite": "#8b4513",
        "lignite-ccs": "#cd853f",
        "nuclear": "#ff6600",
        "oil-heavy": "#000000",
        "oil-light": "#333333",
        "oil-shale": "#555555",
        "h2-ccgt": "#e861e8",
        "h2-fuel-cell": "#b261e8",
        "other-thermal": "#8B4789",
        "other-res": "#4CAF50",
        "hydro-ror": "#298c81",
        "hydro-reservoir": "#1a6b60",
        "hydro-pondage": "#2ecc71",
        "hydro-phs": "#27ae60",
        "hydro-phs-pure": "#16a085",
    }

    tech = data["technologies"][["pypsa_carrier", "co2_tco2_mwh"]].drop_duplicates()
    for _, row in tech.iterrows():
        carrier = row["pypsa_carrier"]
        if carrier in n.carriers.index:
            continue
        n.add(
            "Carrier",
            carrier,
            co2_emissions=row["co2_tco2_mwh"],
            color=CARRIER_COLORS.get(carrier, "#cccccc"),
            nice_name=carrier.replace("-", " ").title(),
        )

    print(f"  Carriers: {len(n.carriers)} added")


def _add_generators(n: pypsa.Network, data: dict) -> None:
    """Add generators (non-storage) to the network."""
    tech = data["technologies"]

    gen_data = data["capacities"][["bus", "index_carrier", "p_nom"]].copy()
    gen_data = gen_data.merge(
        tech[["index_carrier", "pypsa_carrier", "marginal_cost_eur_mwh"]],
        on="index_carrier",
        how="left",
    )

    gen_data = gen_data[~gen_data["pypsa_carrier"].isin(STORAGE_CARRIERS)]
    gen_data = gen_data[~gen_data["pypsa_carrier"].isin(SKIP_GENERATOR_CARRIERS)]

    offshore_cap = data["offshore_cap"]
    offwind_mask = gen_data["index_carrier"] == "offwind"
    gen_data.loc[offwind_mask, "p_nom"] = gen_data[offwind_mask].apply(
        lambda row: offshore_cap.get(row["bus"], row["p_nom"]), axis=1
    )

    added = skipped = 0
    for _, row in gen_data.iterrows():
        bus = row["bus"]
        carrier = row["pypsa_carrier"]
        p_nom = row["p_nom"]
        mc = (
            float(row["marginal_cost_eur_mwh"])
            if pd.notna(row["marginal_cost_eur_mwh"])
            else 0.0
        )

        if pd.isna(carrier) or p_nom <= 0:
            skipped += 1
            continue
        if bus not in n.buses.index or carrier not in n.carriers.index:
            skipped += 1
            continue

        n.add(
            "Generator",
            name=f"{bus}-{row['index_carrier']}",
            bus=bus,
            carrier=carrier,
            p_nom=p_nom,
            p_nom_extendable=False,
            marginal_cost=mc,
            p_max_pu=1.0,
        )
        added += 1

    print(f"  Generators: {added} added (skipped {skipped})")


def _aggregate_storage(storage_raw: pd.DataFrame) -> pd.DataFrame:
    """Aggregate raw storage components into one row per (bus, carrier)."""
    rows = []

    for (bus, carrier), grp in storage_raw.groupby(["bus", "pypsa_carrier"]):
        if "battery" in carrier:
            charge_p = (
                grp[grp["index_carrier"].str.contains("battery-charge", na=False)][
                    "p_nom"
                ]
                .abs()
                .sum()
            )
            discharge_p = grp[
                grp["index_carrier"].str.contains("battery-discharge", na=False)
            ]["p_nom"].sum()
            e_nom = grp[grp["index_carrier"].str.contains("battery-store", na=False)][
                "e_nom"
            ].sum()
            charge_eff = grp[
                grp["index_carrier"].str.contains("battery-charge", na=False)
            ]["efficiency"].mean()
            discharge_eff = grp[
                grp["index_carrier"].str.contains("battery-discharge", na=False)
            ]["efficiency"].mean()

        elif carrier in ("hydro-pondage", "hydro-reservoir"):
            discharge_p = grp[grp["index_carrier"].str.contains("turbine", na=False)][
                "p_nom"
            ].sum()
            charge_p = 0.0
            e_nom = grp[grp["index_carrier"].str.contains("reservoir", na=False)][
                "e_nom"
            ].sum()
            discharge_eff = grp[grp["index_carrier"].str.contains("turbine", na=False)][
                "efficiency"
            ].mean()
            charge_eff = 1.0

        else:
            # hydro-phs / hydro-phs-pure: p_nom sign distinguishes turbine (+) from pump (-)
            # Match notebook: discharge_p = sum of positive p_nom rows,
            #                 charge_p    = abs(sum of negative p_nom rows)
            discharge_p = grp[grp["p_nom"] > 0]["p_nom"].sum()
            charge_p = abs(grp[grp["p_nom"] < 0]["p_nom"].sum())
            e_nom = grp[grp["e_nom"] > 0]["e_nom"].sum()
            discharge_eff = grp[grp["p_nom"] > 0]["efficiency"].mean()
            charge_eff = grp[grp["p_nom"] < 0]["efficiency"].mean()

        # Notebook uses discharge_p as the canonical p_nom for all storage types;
        # e_nom must also be > 0 otherwise the unit is skipped.
        p_nom = discharge_p
        if p_nom <= 0 or e_nom <= 0:
            continue
        rows.append(
            {
                "bus": bus,
                "carrier": carrier,
                "p_nom": p_nom,
                "p_nom_dispatch": discharge_p,
                "p_nom_store": charge_p,
                "max_hours": e_nom / p_nom,
                "efficiency_dispatch": (
                    discharge_eff if not np.isnan(discharge_eff) else 0.9
                ),
                "efficiency_store": charge_eff if not np.isnan(charge_eff) else 0.9,
            }
        )

    return pd.DataFrame(rows)


def _add_storage(
    n: pypsa.Network,
    data: dict,
    battery_scale: float,
    battery_extendable: bool,
    battery_override: pd.DataFrame | None,
) -> None:
    """Add StorageUnit components to the network.

    If battery_override is provided (cols: bus, p_nom_mw, duration_h), it
    replaces the PEMMDB battery capacities for the listed buses.
    """
    tech = data["technologies"]

    storage_raw = data["capacities"][
        data["capacities"]["pypsa_carrier"].isin(STORAGE_CARRIERS)
    ].copy()

    agg = _aggregate_storage(storage_raw)
    if agg.empty:
        print("  Storage: no storage units found")
        return

    bat_override_lookup: dict[str, tuple[float, float]] = {}
    if battery_override is not None:
        for _, row in battery_override.iterrows():
            bat_override_lookup[str(row["bus"])] = (
                float(row["p_nom_mw"]),
                float(row["duration_h"]),
            )

    added = skipped = 0
    for _, row in agg.iterrows():
        bus = row["bus"]
        carrier = row["carrier"]
        p_nom = row["p_nom"]
        max_hours = row["max_hours"]
        eff_d = row["efficiency_dispatch"]
        eff_s = row["efficiency_store"]
        charge_p = row["p_nom_store"]

        if bus not in n.buses.index or carrier not in n.carriers.index:
            skipped += 1
            continue

        is_battery = "battery" in carrier

        if is_battery and bus in bat_override_lookup:
            p_nom_mw, duration_h = bat_override_lookup[bus]
            p_nom = p_nom_mw
            max_hours = duration_h
        elif is_battery:
            p_nom = p_nom * battery_scale
            # max_hours stays the same — it's a ratio, not an absolute value

        if p_nom <= 0:
            skipped += 1
            continue

        # p_min_pu: negative → fraction of p_nom used for charging
        # For pondage/reservoir (charge_p==0), p_min_pu=0 (no pumping).
        # Clamp to [-1, 0] to stay within PyPSA convention.
        if charge_p > 0:
            p_min_pu = max(-charge_p / p_nom, -1.0)
        else:
            p_min_pu = 0.0

        mc_rows = tech[tech["pypsa_carrier"] == carrier]["marginal_cost_eur_mwh"]
        mc = (
            float(mc_rows.iloc[0])
            if not mc_rows.empty and pd.notna(mc_rows.iloc[0])
            else 0.0
        )

        n.add(
            "StorageUnit",
            name=f"{bus}-{carrier}",
            bus=bus,
            carrier=carrier,
            p_nom=p_nom,
            p_nom_extendable=is_battery and battery_extendable,
            p_nom_min=p_nom if is_battery and battery_extendable else 0.0,
            p_min_pu=p_min_pu,
            max_hours=max_hours,
            efficiency_dispatch=eff_d,
            efficiency_store=eff_s,
            marginal_cost=mc,
            cyclic_state_of_charge=True,
        )
        added += 1

    print(f"  Storage: {added} units added (skipped {skipped})")


def _add_snapshots_and_demand(
    n: pypsa.Network,
    data: dict,
    load_scale: float,
) -> None:
    """Set 8760 hourly snapshots for 2030 and add load time series."""
    snapshots = pd.date_range("2030-01-01", periods=8760, freq="h")
    n.set_snapshots(snapshots)

    demand = data["electricity_demand"].copy()
    demand.index = snapshots

    loads_added = 0
    for bus in n.buses.index:
        if bus not in demand.columns:
            continue
        n.add("Load", name=f"{bus}-load", bus=bus, p_set=demand[bus] * load_scale)
        loads_added += 1

    print(
        f"  Snapshots: {len(n.snapshots)} hours | "
        f"Loads: {loads_added} buses (load_scale={load_scale})"
    )


def _add_vre_profiles(n: pypsa.Network, data: dict) -> None:
    """Assign p_max_pu time series to wind and solar generators."""
    profile_map = {
        "onwind": data["wind_onshore"],
        "offwind": data["wind_offshore"],
        "solar-pv-utility": data["solar_utility"],
        "solar-pv-rooftop": data["solar_rooftop"],
    }

    applied = fallback = 0
    for gen_name in n.generators.index:
        carrier = n.generators.loc[gen_name, "carrier"]
        if carrier not in profile_map:
            continue
        bus = n.generators.loc[gen_name, "bus"]
        df = profile_map[carrier]
        if bus in df.columns:
            n.generators_t.p_max_pu[gen_name] = df[bus].values
            applied += 1
        else:
            n.generators_t.p_max_pu[gen_name] = 0  # FALLBACK_CF
            fallback += 1

    print(
        f"  VRE profiles: {applied} applied, {fallback} fallback (CF={0})"
    )  # FALLBACK_CF


def _add_thermal_availability(n: pypsa.Network) -> None:
    """Set p_max_pu=1.0 for all conventional thermal generators."""
    # thermal_set = set(CONVENTIONAL_CARRIERS)
    thermal_set = [
        c
        for c in CONVENTIONAL_CARRIERS
        if c not in ("electrolyser", "h2", "other-res", "nuclear")
    ]

    count = 0
    for gen_name in n.generators.index:
        if n.generators.loc[gen_name, "carrier"] in thermal_set:
            n.generators_t.p_max_pu[gen_name] = 1.0
            count += 1
    print(f"  Thermal availability: {count} generators set to p_max_pu=1.0")


def _add_nuclear_profiles(
    n: pypsa.Network,
    data: dict,
    nuclear_p_max_pu_override: pd.DataFrame | None,
) -> None:
    """Assign hourly nuclear capacity factor profiles.

    Uses nuclear_p_max_pu_override if provided, otherwise falls back to the
    nuclear_p_max_pu_2030.csv data.  Profile timestamps are remapped to the
    snapshot year by replacing the year component.
    """
    profiles = (
        nuclear_p_max_pu_override.copy()
        if nuclear_p_max_pu_override is not None
        else data["nuclear_profiles"].copy()
    )

    snapshot_year = n.snapshots[0].year
    profiles.index = [t.replace(year=snapshot_year) for t in profiles.index]
    profiles = profiles.loc[profiles.index.isin(n.snapshots)]

    applied = fallback = 0
    for gen_name in n.generators.index:
        if n.generators.loc[gen_name, "carrier"] != "nuclear":
            continue
        bus = n.generators.loc[gen_name, "bus"]
        if bus in profiles.columns and len(profiles) == len(n.snapshots):
            n.generators_t.p_max_pu[gen_name] = profiles[bus].values
            applied += 1
        else:
            n.generators_t.p_max_pu[gen_name] = 0.8  # FALLBACK_CF
            fallback += 1

    print(
        f"  Nuclear profiles: {applied} applied, {fallback} fallback (CF={0.8})"
    )  # FALLBACK_CF


def _add_other_res_profiles(n: pypsa.Network, data: dict) -> None:
    """Assign p_max_pu profiles to other-res generators."""
    other_res_pmax = data["other_res_pmax"].rename(
        columns={"UK00": "GB00", "UKNI": "GBNI"}
    )
    other_res_pmax.index = n.snapshots

    applied = fallback = 0
    for gen_name in n.generators.index:
        if n.generators.loc[gen_name, "carrier"] != "other-res":
            continue
        bus = n.generators.loc[gen_name, "bus"]
        if bus in other_res_pmax.columns:
            n.generators_t.p_max_pu[gen_name] = other_res_pmax[bus].values
            applied += 1
        else:
            n.generators_t.p_max_pu[gen_name] = 0.6  # FALLBACK_CF
            fallback += 1

    print(
        f"  Other-res profiles: {applied} applied, {fallback} fallback (CF={0.6})"
    )  # FALLBACK_CF


def _add_hydro_ror_profiles(n: pypsa.Network, data: dict) -> None:
    """Apply run-of-river capacity factor profiles (inflow / p_nom)."""
    hydro_ror = data["hydro_ror"].copy()
    hydro_ror.index = n.snapshots

    applied = skipped = 0
    for gen_name in n.generators.index:
        if n.generators.loc[gen_name, "carrier"] not in HYDRO_ROR_CARRIERS:
            continue
        bus = n.generators.loc[gen_name, "bus"]
        p_nom = n.generators.loc[gen_name, "p_nom"]
        if bus in hydro_ror.columns and p_nom > 0:
            n.generators_t.p_max_pu[gen_name] = (hydro_ror[bus].values / p_nom).clip(
                0.0, 1.0
            )
            applied += 1
        else:
            skipped += 1

    print(f"  Hydro RoR profiles: {applied} applied, {skipped} skipped")


def _add_hydro_inflows(n: pypsa.Network, data: dict) -> None:
    """Apply natural inflow time series to hydro StorageUnits."""
    inflow_map = {
        "hydro-reservoir": data.get("hydro_reservoir"),
        "hydro-pondage": data.get("hydro_pondage"),
        "hydro-phs": data.get("hydro_ps_open"),
    }

    applied = skipped = 0

    for carrier, inflow_df in inflow_map.items():
        if inflow_df is None or inflow_df.empty:
            continue
        inflow_df = inflow_df.copy()
        inflow_df.index = n.snapshots

        units = n.storage_units[n.storage_units["carrier"] == carrier]
        if units.empty:
            continue

        bus_total = units.groupby("bus")["p_nom"].sum()

        for su_name, row in units.iterrows():
            bus = row["bus"]
            if bus not in inflow_df.columns:
                skipped += 1
                continue
            total_p = bus_total[bus]
            if total_p <= 0:
                skipped += 1
                continue
            share = row["p_nom"] / total_p
            n.storage_units_t.inflow[su_name] = (inflow_df[bus].values * share).clip(
                min=0
            )
            applied += 1

    print(f"  Hydro inflows: {applied} storage units with inflow, {skipped} skipped")


def _add_dsr(n: pypsa.Network, data: dict) -> None:
    """Add Demand Side Response generators.

    Reads ``data["dsr_static"]`` (already filtered by climate year by
    load_network_data) and ``data["dsr_ts"]``.  Prints the climate year label
    stored in ``data["climate_year"]``.
    """
    climate_year = data.get("climate_year", "?")

    dsr_df = data["dsr_static"].copy()
    dsr_df = dsr_df[dsr_df["Capacity"] > 0].copy()
    dsr_df["node"] = dsr_df["node"].replace(DSR_NODE_MAP)

    dsr_ts = data["dsr_ts"].copy()
    dsr_ts.index = n.snapshots
    dsr_ts.columns = [
        DSR_NODE_MAP.get(c.split("_")[0], c.split("_")[0])
        + "_"
        + "_".join(c.split("_")[1:])
        for c in dsr_ts.columns
    ]

    dsr_by_node = dsr_df.groupby("node")
    added = skipped = 0

    for bus in n.buses.index:
        if bus not in dsr_by_node.groups:
            continue

        for _, row in dsr_by_node.get_group(bus).iterrows():
            price_band = row["price_band"]
            ts_col = f"{bus}_{price_band}"

            if ts_col not in dsr_ts.columns:
                skipped += 1
                continue

            n.add(
                "Generator",
                name=f"{bus}-dsr-{price_band.replace(' ', '_')}",
                bus=bus,
                carrier="dsr",
                p_nom=row["Capacity"],
                p_nom_extendable=False,
                marginal_cost=row["Price"],
                p_max_pu=dsr_ts[ts_col],
            )
            added += 1

    print(f"  DSR (CY{climate_year}): {added} generators added, {skipped} skipped")


def _add_slack(n: pypsa.Network, slack_cost: float) -> None:
    """Add one slack generator per bus with very high marginal cost.

    Slack generators ensure the LP is always feasible.  A high slack_cost
    (e.g. 3000 EUR/MWh) places them last in the merit order so their dispatch
    signals infeasibility.
    """
    n.add(
        "Carrier",
        "slack",
        nice_name="Slack (deficit)",
        color="#ff0000",
        co2_emissions=0.0,
    )

    added = 0
    for bus in n.buses.index:
        name = f"{bus}-slack"
        if name not in n.generators.index:
            n.add(
                "Generator",
                name=name,
                bus=bus,
                carrier="slack",
                p_nom=1e9,
                p_nom_extendable=False,
                marginal_cost=slack_cost,
                p_min_pu=0.0,
                p_max_pu=1.0,
            )
            added += 1

    print(f"  Slack: {added} generators added (cost={slack_cost} EUR/MWh)")


# ===========================================================================
# Public API
# ===========================================================================


def build_network(
    data: dict,
    ntc_scale: float = 1.0,
    load_scale: float = 1.0,
    battery_scale: float = 1.0,
    battery_override: pd.DataFrame | None = None,
    nuclear_p_max_pu: pd.DataFrame | None = None,
    ntc_override: pd.DataFrame | None = None,
    battery_extendable: bool = False,
    slack_cost: float = 3_000.0,
    output_path: str | None = None,
) -> pypsa.Network:
    """Build and return an unsolved PyPSA TYNDP 2030 network.

    All time series, capacities, and profiles must already be loaded into the
    *data* dict by ``load_network_data()``.  This function performs no file I/O.

    Parameters
    ----------
    data :
        Dict of DataFrames returned by ``load_network_data()``.  Required keys:
        buses, links, capacities, technologies, offshore_cap, nuclear_profiles,
        other_res_pmax, dsr_static, dsr_ts, wind_onshore, wind_offshore,
        solar_utility, solar_rooftop, electricity_demand, hydro_ror,
        hydro_reservoir, hydro_pondage, hydro_ps_open, climate_year.
    ntc_scale :
        Multiplier for all DC link capacities (default 1.0).
        Ignored for links listed in ntc_override.
    load_scale :
        Multiplier applied to all load time series (default 1.0).
    battery_scale :
        Multiplier for battery p_nom and e_nom when no battery_override entry
        exists for that bus (default 1.0).
    battery_override :
        DataFrame with columns [bus, p_nom_mw, duration_h].  Replaces PEMMDB
        battery capacities entirely for the listed buses.
    nuclear_p_max_pu :
        DataFrame with hourly index and bus columns.  Overrides the default
        nuclear_p_max_pu_2030.csv profiles when provided.
    ntc_override :
        DataFrame with columns [link_id, p_nom].  Sets individual link
        capacities, overriding both the CSV value and ntc_scale for those links.
    battery_extendable :
        If True, batteries are p_nom_extendable (investment mode).
    slack_cost :
        Marginal cost for slack generators in EUR/MWh (default 3000).
        Pass None to skip slack entirely.
    output_path :
        Optional .nc export path for the finished network.

    Returns
    -------
    pypsa.Network
        Fully built, unsolved network.
    """
    climate_year = data.get("climate_year", "?")

    print(
        f"Building network (CY={climate_year}, ntc_scale={ntc_scale}, "
        f"load_scale={load_scale}, battery_scale={battery_scale}, "
        f"battery_extendable={battery_extendable}, slack_cost={slack_cost})"
    )

    # Step 1: topology
    print("[1/6] Building topology...")
    n = pypsa.Network()
    n.name = "TYNDP2024_Electricity_2030"
    n.set_snapshots(pd.date_range("2030-01-01", periods=1, freq="h"))
    _build_topology(n, data, ntc_scale, ntc_override)

    # Step 2: carriers
    print("[2/6] Adding carriers...")
    _add_carriers(n, data)

    # Step 3: generators + storage
    print("[3/6] Adding generators and storage...")
    _add_generators(n, data)
    _add_storage(n, data, battery_scale, battery_extendable, battery_override)

    # Step 4: snapshots + demand
    print("[4/6] Setting snapshots and demand...")
    _add_snapshots_and_demand(n, data, load_scale)

    # Step 5: time series profiles
    print("[5/6] Applying time series profiles...")
    _add_vre_profiles(n, data)
    _add_thermal_availability(n)
    _add_nuclear_profiles(n, data, nuclear_p_max_pu)
    _add_other_res_profiles(n, data)
    _add_hydro_ror_profiles(n, data)
    _add_hydro_inflows(n, data)

    # Step 6: DSR + slack
    print("[6/6] Adding DSR and slack...")
    _add_dsr(n, data)
    if slack_cost is not None:
        _add_slack(n, slack_cost)
    else:
        print("  Slack: skipped (slack_cost=None)")

    print("\nNetwork ready:")
    print(
        f"  buses={len(n.buses)}, links={len(n.links)}, "
        f"generators={len(n.generators)}, "
        f"storage_units={len(n.storage_units)}, "
        f"loads={len(n.loads)}, snapshots={len(n.snapshots)}"
    )

    if output_path is not None:
        n.export_to_netcdf(output_path)
        print(f"  Exported to {output_path}")

    return n
