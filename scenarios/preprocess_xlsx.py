"""
preprocess_xlsx.py
==================
One-time conversion of all slow source files (XLSX + PECD CSVs) to fast Parquet.

Raw bus/zone names are preserved as-is (no node remapping) — load_network_data.py
applies all remapping exactly as before.

Run once from the project root before running run_scenarios.py:
    python scenarios/preprocess_xlsx.py

Outputs (written to data/tyndp2024/preprocessed/):
    demand_profiles.parquet       — hourly demand, all buses × all climate years
    hydro_inflows.parquet         — hourly hydro inflows, all buses/types × all CYs
    pecd_wind_onshore.parquet     — hourly onshore wind CF, all buses × all CYs
    pecd_wind_offshore.parquet    — hourly offshore wind CF (zone-level) × all CYs
    pecd_solar_utility.parquet    — hourly utility solar CF, all buses × all CYs
    pecd_solar_rooftop.parquet    — hourly rooftop solar CF, all buses × all CYs
    pecd_solar_generic.parquet    — hourly generic LFSolarPV CF, all buses × all CYs

After this script runs, load_network_data.py will automatically use the
parquet files instead of original XLSX/CSV files (~10x faster loading).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TYNDP_DIR = ROOT / "data" / "tyndp2024"
OUT_DIR = TYNDP_DIR / "preprocessed"
OUT_DIR.mkdir(exist_ok=True)


# ===========================================================================
# Helpers
# ===========================================================================


_PECD_HEADER_ROWS = 10

_HYDRO_SHEET_CONFIGS = {
    "ror":       ("Run of River - Year Dependent",  "Energy Inflow [GWh/day]",  "day"),
    "reservoir": ("Reservoir - Year Dependent",      "Energy Inflow [GWh/week]", "week"),
    "pondage":   ("Pondage - Year Dependent",         "Energy Inflow [GWh/day]",  "day"),
    "ps_open":   ("PS Open - Year Dependent",         "Energy Inflow [GWh/week]", "week"),
}

_PECD_SLUGS = {
    "wind_onshore":  "Wind_Onshore",
    "wind_offshore": "Wind_Offshore",
    "solar_utility": "LFSolarPVUtility",
    "solar_rooftop": "LFSolarPVRooftop",
    "solar_generic": "LFSolarPV",
}


def _read_pecd_all_years(path: Path) -> pd.DataFrame:
    """Read one PECD CSV, return all climate years.

    Returns DataFrame: index = hour (0..8759), columns = climate_year (int).
    Raw bus/zone names preserved — no node remapping.
    """
    df = pd.read_csv(path, header=None, skiprows=_PECD_HEADER_ROWS)
    df.columns = df.iloc[0]
    df = df.drop(df.index[0]).reset_index(drop=True)

    result = {}
    for col in df.columns:
        if not isinstance(col, float) or not (1980 <= int(col) <= 2025):
            continue
        cy = int(col)
        values = pd.to_numeric(df[col], errors="coerce").fillna(0.0).values
        if len(values) > 8760:
            values = values[:8760]
        elif len(values) < 8760:
            values = np.pad(values, (0, 8760 - len(values)))
        result[cy] = values.clip(0.0, 1.0)

    return pd.DataFrame(result)


def _daily_to_hourly(values: np.ndarray) -> np.ndarray:
    return np.repeat(np.asarray(values, dtype=float)[:365], 24) * (1_000.0 / 24.0)


def _weekly_to_hourly(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=float)[:52]
    hourly = np.repeat(arr, 168)
    pad = 8760 - len(hourly)
    if pad > 0:
        hourly = np.append(hourly, np.repeat(arr[-1], pad))
    return hourly[:8760] * (1_000.0 / 168.0)


def _stack_to_parquet(bus_frames: dict[str, pd.DataFrame], out_path: Path) -> None:
    """Stack {bus: DataFrame(index=hour, columns=cy)} → MultiIndex(cy, hour) parquet."""
    combined = pd.concat(bus_frames, axis=1)   # cols: MultiIndex(bus, cy)
    combined = combined.stack(level=1)          # adds cy to index → (hour, cy)
    combined.index.names = ["hour", "climate_year"]
    combined = combined.reorder_levels(["climate_year", "hour"]).sort_index()
    combined.to_parquet(out_path)


# ===========================================================================
# Demand
# ===========================================================================


def preprocess_demand() -> None:
    out_path = OUT_DIR / "demand_profiles.parquet"
    if out_path.exists():
        print(f"  SKIP demand_profiles (already exists)")
        return

    xl_path = (
        TYNDP_DIR / "Demand Profiles" / "NT"
        / "Electricity demand profiles" / "2030_National Trends.xlsx"
    )
    print(f"  Reading {xl_path.name} ...")
    t0 = time.time()
    xl = pd.ExcelFile(xl_path, engine="openpyxl")

    df0 = pd.read_excel(xl, sheet_name=xl.sheet_names[0], header=7, engine="openpyxl")
    climate_years = sorted(
        int(c) for c in df0.columns
        if isinstance(c, (int, float)) and 1980 <= int(c) <= 2025
    )

    bus_frames: dict[str, pd.DataFrame] = {}
    for sheet in xl.sheet_names:
        df = pd.read_excel(xl, sheet_name=sheet, header=7, engine="openpyxl")
        frame = {}
        for cy in climate_years:
            cy_col = cy if cy in df.columns else float(cy)
            if cy_col not in df.columns:
                continue
            values = pd.to_numeric(df[cy_col], errors="coerce").fillna(0.0).values[:8760]
            if len(values) < 8760:
                values = np.pad(values, (0, 8760 - len(values)))
            frame[cy] = values
        if frame:
            bus_frames[sheet] = pd.DataFrame(frame)  # raw sheet name, no remapping

    _stack_to_parquet(bus_frames, out_path)
    print(f"  Saved demand_profiles.parquet  ({time.time() - t0:.0f}s)")


# ===========================================================================
# Hydro
# ===========================================================================


def preprocess_hydro() -> None:
    out_path = OUT_DIR / "hydro_inflows.parquet"
    if out_path.exists():
        print(f"  SKIP hydro_inflows (already exists)")
        return

    hydro_dir = TYNDP_DIR / "Hydro Inflows" / "2030"
    xl_paths = sorted(hydro_dir.glob("PEMMDB_*_Hydro_Inflows_2030.xlsx"))
    print(f"  Reading {len(xl_paths)} hydro XLSX files ...")
    t0 = time.time()

    # bus → hydro_type → DataFrame(index=hour, cols=cy)
    all_frames: dict[tuple[str, str], pd.DataFrame] = {}

    for i, xl_path in enumerate(xl_paths):
        m = re.match(r"PEMMDB_([A-Z0-9]+)_Hydro_Inflows_2030\.xlsx", xl_path.name)
        if not m:
            continue
        bus = m.group(1)  # raw name, no remapping
        xl = pd.ExcelFile(xl_path, engine="openpyxl")

        climate_years = None
        for sheet_name, _, _ in _HYDRO_SHEET_CONFIGS.values():
            if sheet_name in xl.sheet_names:
                df0 = pd.read_excel(xl, sheet_name=sheet_name, header=1, engine="openpyxl")
                climate_years = sorted(
                    int(c) for c in df0.columns
                    if isinstance(c, (int, float)) and 1980 <= int(c) <= 2025
                )
                break
        if climate_years is None:
            continue

        for hydro_type, (sheet_name, var_label, time_res) in _HYDRO_SHEET_CONFIGS.items():
            if sheet_name not in xl.sheet_names:
                continue
            df = pd.read_excel(xl, sheet_name=sheet_name, header=1, engine="openpyxl")
            inflow_rows = df[df["Variable"].astype(str).str.strip() == var_label]
            if inflow_rows.empty:
                continue
            frame = {}
            for cy in climate_years:
                cy_col = cy if cy in df.columns else float(cy)
                if cy_col not in df.columns:
                    continue
                values = pd.to_numeric(inflow_rows[cy_col], errors="coerce").fillna(0.0).values
                hourly = _daily_to_hourly(values) if time_res == "day" else _weekly_to_hourly(values)
                frame[cy] = hourly
            if frame:
                all_frames[(hydro_type, bus)] = pd.DataFrame(frame)

        if (i + 1) % 10 == 0:
            print(f"    {i + 1}/{len(xl_paths)} files  ({time.time() - t0:.0f}s)")

    # Build MultiIndex columns: (hydro_type, bus)
    combined = pd.concat(
        {key: df for key, df in all_frames.items()}, axis=1
    )  # cols: 3-level MultiIndex((hydro_type, bus), cy)
    combined = combined.stack(level=2)  # stack climate_year into index → (hour, climate_year)
    combined.index.names = ["hour", "climate_year"]
    combined = combined.reorder_levels(["climate_year", "hour"]).sort_index()
    combined.columns.names = ["hydro_type", "bus"]
    combined.to_parquet(out_path)
    print(f"  Saved hydro_inflows.parquet  ({time.time() - t0:.0f}s)")


# ===========================================================================
# PECD (wind + solar)
# ===========================================================================


def preprocess_pecd() -> None:
    pecd_dir = TYNDP_DIR / "PECD 2030"

    for key, slug in _PECD_SLUGS.items():
        out_path = OUT_DIR / f"pecd_{key}.parquet"
        if out_path.exists():
            print(f"  SKIP pecd_{key} (already exists)")
            continue

        pattern = re.compile(
            rf"^PECD_{re.escape(slug)}_2030_([A-Z0-9]+)_edition 2023\.2\.csv$"
        )
        files = sorted(pecd_dir.glob(f"PECD_{slug}_2030_*_edition 2023.2.csv"))
        print(f"  Reading {len(files)} PECD files for {key} ...")
        t0 = time.time()

        bus_frames: dict[str, pd.DataFrame] = {}
        for f in files:
            m = pattern.match(f.name)
            if not m:
                continue
            bus = m.group(1)  # raw name, no remapping
            bus_frames[bus] = _read_pecd_all_years(f)

        if not bus_frames:
            print(f"  No files found for {key}, skipping")
            continue

        _stack_to_parquet(bus_frames, out_path)
        print(f"  Saved pecd_{key}.parquet  ({time.time() - t0:.0f}s)")


# ===========================================================================
# Main
# ===========================================================================

if __name__ == "__main__":
    print("=== Preprocessing source files → Parquet ===")
    print(f"Output dir: {OUT_DIR}\n")

    print("[1/3] Demand profiles...")
    preprocess_demand()

    print("\n[2/3] Hydro inflows...")
    preprocess_hydro()

    print("\n[3/3] PECD profiles (wind + solar)...")
    preprocess_pecd()

    print("\nDone. Run run_scenarios.py to build networks.")
