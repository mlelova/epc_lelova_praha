"""
load_network_data.py
====================
Loads all input data needed to build the TYNDP 2030 PyPSA network and returns
it as a plain dict of DataFrames.  No network construction happens here.

The returned dict is passed directly to build_network() in build_network.py.

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
        coal_price=10.0,
    )

    n = build_network(data)
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd


# PECD technology slugs used in raw file names
_PECD_ONSHORE_SLUG = "Wind_Onshore"
_PECD_OFFSHORE_SLUG = "Wind_Offshore"
_PECD_UTILITY_SLUG = "LFSolarPVUtility"
_PECD_ROOFTOP_SLUG = "LFSolarPVRooftop"

_PECD_PARQUET_FILES = {
    "wind_onshore": "pecd_wind_onshore.parquet",
    "wind_offshore": "pecd_wind_offshore.parquet",
    "solar_utility": "pecd_solar_utility.parquet",
    "solar_rooftop": "pecd_solar_rooftop.parquet",
    "solar_generic": "pecd_solar_generic.parquet",
}

# Number of metadata rows before the data table in every PECD CSV
_PECD_HEADER_ROWS = 10

# TYNDP uses UK00/UKNI in raw data files; PyPSA network uses GB00/GBNI
_NODE_MAP = {"UK00": "GB00", "UKNI": "GBNI"}

# Offshore zone names in offshore_wind_capacity_by_bus.csv use GB-prefix,
# but the actual PECD files use UK-prefix.  This reverse map translates
# zone codes from the CSV to the file-system names before doing path lookups.
_ZONE_FILE_MAP = {
    "GBOH003": "UKOH003",
    "GBOH004": "UKOH004",
    "GBOH005": "UKOH005",
    "GBOH006": "UKOH006",
    "GBOR001": "UKOR001",
    "GBNIOR1": "UKNIOR1",
    "GB00": "UK00",
    "GBNI": "UKNI",
}


# ===========================================================================
# Fixed data (not climate-year dependent)
# ===========================================================================


def _load_fixed(data_dir: Path) -> dict:
    """Load all pre-processed files from data_dir that do not vary by climate year."""
    d = data_dir

    technologies = pd.read_csv(d / "technologies_2030.csv")

    # Merge pypsa_carrier AND efficiency from technologies into capacities.
    # The pemmdb_capacities_2030_grouped.csv already has an "efficiency" column
    # but it contains wrong values (e.g. 1.00 for PHS, 0.92 for battery).
    # The authoritative values are in technologies_2030.csv (0.90 / 0.96).
    # We drop the bad column and replace it with the correct one from technologies.
    capacities = pd.read_csv(d / "pemmdb_capacities_2030_grouped.csv")
    # Drop existing efficiency column (it may have wrong values from PEMMDB)
    capacities = capacities.drop(columns=["efficiency"], errors="ignore")
    tech_meta = technologies[
        ["index_carrier", "pypsa_carrier", "efficiency"]
    ].drop_duplicates("index_carrier")
    capacities = capacities.merge(tech_meta, on="index_carrier", how="left")

    return {
        "buses": pd.read_csv(d / "buses.csv"),
        "links": pd.read_csv(
            d / "links.csv",
            names=[
                "link_id",
                "bus0",
                "bus1",
                "voltage",
                "p_nom",
                "length",
                "underground",
                "under_construction",
                "tags",
                "geometry_a",
                "geometry_b",
            ],
            skiprows=1,
            index_col=0,
        ),
        "capacities": capacities,
        "technologies": technologies,
        "offshore_cap": pd.read_csv(
            d / "offshore_wind_capacity_by_bus.csv", index_col="bus"
        )["total_existing_mw"],
        "offshore_cap_df": pd.read_csv(d / "offshore_wind_capacity_by_bus.csv"),
        "nuclear_profiles": pd.read_csv(
            d / "nuclear_p_max_pu_2030.csv", index_col=0, parse_dates=True
        ),
        "other_res_pmax": pd.read_csv(
            d / "other_res_p_max_pu.csv", index_col=0, parse_dates=True
        ),
        "dsr_static": pd.read_csv(d / "dsr_pemmdb.csv"),
        "dsr_ts": pd.read_csv(
            d / "dsr_p_max_pu_timeseries.csv", index_col=0, parse_dates=True
        ),
    }


# ===========================================================================
# PECD (wind / solar) loaders
# ===========================================================================


def _read_pecd_csv(path: Path, climate_year: int) -> np.ndarray:
    """Read one PECD raw CSV and return the hourly capacity factor array for climate_year.

    PECD files have 10 metadata rows followed by a data table with columns:
        Date, Hour, 1982.0, 1983.0, ..., 2019.0  (float column labels)

    Returns ndarray of length 8760, values clipped to [0, 1].
    """
    df = pd.read_csv(path, header=None, skiprows=_PECD_HEADER_ROWS)
    df.columns = df.iloc[0]
    df = df.drop(df.index[0]).reset_index(drop=True)

    cy_col = float(climate_year)
    if cy_col not in df.columns:
        raise KeyError(
            f"Climate year {climate_year} not found in {path.name}. "
            f"Available: {[c for c in df.columns if isinstance(c, float)]}"
        )

    values = pd.to_numeric(df[cy_col], errors="coerce").fillna(0.0).values
    if len(values) > 8760:
        values = values[:8760]
    elif len(values) < 8760:
        values = np.pad(values, (0, 8760 - len(values)))
    return values.clip(0.0, 1.0)


def _load_pecd_onshore(pecd_dir: Path, climate_year: int) -> pd.DataFrame:
    """Onshore wind capacity factors for all buses, one climate year.

    Returns DataFrame: index = 8760 hourly 2030 timestamps, columns = bus codes.
    """
    pattern = re.compile(
        rf"^PECD_{re.escape(_PECD_ONSHORE_SLUG)}_2030_([A-Z0-9]+)_edition 2023\.2\.csv$"
    )
    bus_data = {}
    for f in sorted(
        pecd_dir.glob(f"PECD_{_PECD_ONSHORE_SLUG}_2030_*_edition 2023.2.csv")
    ):
        m = pattern.match(f.name)
        if m:
            bus_data[m.group(1)] = _read_pecd_csv(f, climate_year)

    df = pd.DataFrame(bus_data)
    df.index = pd.date_range("2030-01-01", periods=8760, freq="h")
    return df.rename(columns=_NODE_MAP)


def _load_pecd_solar(pecd_dir: Path, climate_year: int, slug: str) -> pd.DataFrame:
    """Solar (utility or rooftop) capacity factors for all buses, one climate year.

    Italian regions have type-specific files (LFSolarPVUtility / LFSolarPVRooftop).
    All other buses use the generic LFSolarPV file for both utility and rooftop.

    Returns DataFrame: index = 8760 hourly 2030 timestamps, columns = bus codes.
    """
    bus_data: dict[str, np.ndarray] = {}

    # 1. Load type-specific files first (Italian regions: ITCA, ITCN, etc.)
    pattern_specific = re.compile(
        rf"^PECD_{re.escape(slug)}_2030_([A-Z0-9]+)_edition 2023\.2\.csv$"
    )
    for f in sorted(pecd_dir.glob(f"PECD_{slug}_2030_*_edition 2023.2.csv")):
        m = pattern_specific.match(f.name)
        if m:
            bus_data[m.group(1)] = _read_pecd_csv(f, climate_year)

    # 2. Load generic LFSolarPV files for buses NOT already covered by type-specific files.
    #    These generic files apply to both utility and rooftop for all non-Italian buses.
    pattern_generic = re.compile(
        r"^PECD_LFSolarPV_2030_([A-Z0-9]+)_edition 2023\.2\.csv$"
    )
    for f in sorted(pecd_dir.glob("PECD_LFSolarPV_2030_*_edition 2023.2.csv")):
        m = pattern_generic.match(f.name)
        if m:
            bus = m.group(1)
            if bus not in bus_data:
                bus_data[bus] = _read_pecd_csv(f, climate_year)

    df = pd.DataFrame(bus_data)
    df.index = pd.date_range("2030-01-01", periods=8760, freq="h")
    return df.rename(columns=_NODE_MAP)


def _load_pecd_offshore(
    pecd_dir: Path,
    climate_year: int,
    offshore_cap_df: pd.DataFrame,
) -> pd.DataFrame:
    """Offshore wind capacity factors aggregated to bus level, one climate year.

    Zone-level PECD profiles are combined with capacity weighting using the
    zone breakdown in offshore_wind_capacity_by_bus.csv (column 'zones').
    Falls back to a bus-level file if no zone files are found.

    Returns DataFrame: index = 8760 hourly 2030 timestamps, columns = bus codes.
    """
    # Parse zone breakdown: "BEOH001 (5760 MW); DEOH001 (18868 MW); ..."
    zone_info: dict[str, list[tuple[str, float]]] = {}
    for _, row in offshore_cap_df.iterrows():
        bus = row["bus"]
        raw = str(row.get("zones", ""))
        zones_and_caps: list[tuple[str, float]] = []
        for item in raw.split(";"):
            item = item.strip()
            m = re.match(r"^(\S+)\s+\((\d+(?:\.\d+)?)\s*MW\)$", item)
            if m:
                zones_and_caps.append((m.group(1), float(m.group(2))))
        if zones_and_caps:
            zone_info[bus] = zones_and_caps

    bus_profiles: dict[str, np.ndarray] = {}

    for _, row in offshore_cap_df.iterrows():
        bus = row["bus"]
        total_mw = float(row.get("total_existing_mw", 0.0))
        if total_mw <= 0:
            continue

        if bus in zone_info:
            weighted_sum = np.zeros(8760)
            total_weight = 0.0
            for zone, cap_mw in zone_info[bus]:
                # Zone names in the CSV may use GB-prefix; map to file-system UK-prefix
                file_zone = _ZONE_FILE_MAP.get(zone, zone)
                zone_path = (
                    pecd_dir
                    / f"PECD_{_PECD_OFFSHORE_SLUG}_2030_{file_zone}_edition 2023.2.csv"
                )
                if zone_path.exists():
                    weighted_sum += _read_pecd_csv(zone_path, climate_year) * cap_mw
                    total_weight += cap_mw
            if total_weight > 0:
                bus_profiles[bus] = weighted_sum / total_weight
                continue

        # Fallback: bus-level file (also apply zone file map for bus name)
        file_bus = _ZONE_FILE_MAP.get(bus, bus)
        bus_path = (
            pecd_dir / f"PECD_{_PECD_OFFSHORE_SLUG}_2030_{file_bus}_edition 2023.2.csv"
        )
        if bus_path.exists():
            bus_profiles[bus] = _read_pecd_csv(bus_path, climate_year)

    df = pd.DataFrame(bus_profiles)
    df.index = pd.date_range("2030-01-01", periods=8760, freq="h")
    return df


def _read_pecd_parquet(path: Path, climate_year: int) -> pd.DataFrame:
    """Read and validate one climate year from a preprocessed PECD parquet."""
    try:
        df = pd.read_parquet(
            path,
            filters=[("climate_year", "==", climate_year)],
        )
    except Exception as exc:
        raise ValueError(f"Could not read PECD parquet {path}: {exc}") from exc

    if df.empty:
        try:
            available_index = pd.read_parquet(path, columns=[]).index
            available = sorted(
                set(available_index.get_level_values("climate_year").astype(int))
            )
        except Exception:
            available = []
        raise KeyError(
            f"Climate year {climate_year} not found in {path.name}. "
            f"Available: {available}"
        )

    if not isinstance(df.index, pd.MultiIndex) or set(df.index.names) != {
        "climate_year",
        "hour",
    }:
        raise ValueError(
            f"{path.name} must use a MultiIndex named climate_year,hour"
        )

    df = df.droplevel("climate_year").sort_index()
    if len(df) != 8760 or not df.index.equals(pd.Index(range(8760), name="hour")):
        raise ValueError(
            f"{path.name} climate year {climate_year} must contain hours 0..8759; "
            f"found {len(df)} rows"
        )
    if df.shape[1] == 0:
        raise ValueError(f"{path.name} contains no profile columns")

    numeric = df.apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise ValueError(
            f"{path.name} climate year {climate_year} contains missing/non-numeric values"
        )
    if (numeric < -1e-6).any().any() or (numeric > 1 + 1e-6).any().any():
        raise ValueError(f"{path.name} capacity factors must be within [0, 1]")

    numeric = numeric.clip(0.0, 1.0)
    numeric.index = pd.date_range("2030-01-01", periods=8760, freq="h")
    return numeric


def _aggregate_pecd_offshore_parquet(
    profiles: pd.DataFrame,
    offshore_cap_df: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate raw offshore zone columns to modeled buses."""
    zone_info: dict[str, list[tuple[str, float]]] = {}
    for _, row in offshore_cap_df.iterrows():
        zones_and_caps: list[tuple[str, float]] = []
        for item in str(row.get("zones", "")).split(";"):
            m = re.match(r"^(\S+)\s+\((\d+(?:\.\d+)?)\s*MW\)$", item.strip())
            if m:
                zones_and_caps.append((m.group(1), float(m.group(2))))
        if zones_and_caps:
            zone_info[str(row["bus"])] = zones_and_caps

    bus_profiles: dict[str, np.ndarray] = {}
    for _, row in offshore_cap_df.iterrows():
        bus = str(row["bus"])
        if float(row.get("total_existing_mw", 0.0)) <= 0:
            continue

        weighted_sum = np.zeros(len(profiles))
        total_weight = 0.0
        for zone, cap_mw in zone_info.get(bus, []):
            source_zone = _ZONE_FILE_MAP.get(zone, zone)
            if source_zone in profiles.columns:
                weighted_sum += profiles[source_zone].to_numpy() * cap_mw
                total_weight += cap_mw
        if total_weight > 0:
            bus_profiles[bus] = weighted_sum / total_weight
            continue

        source_bus = _ZONE_FILE_MAP.get(bus, bus)
        if source_bus in profiles.columns:
            bus_profiles[bus] = profiles[source_bus].to_numpy()

    return pd.DataFrame(bus_profiles, index=profiles.index)


def _merge_pecd_solar(
    specific: pd.DataFrame,
    generic: pd.DataFrame,
) -> pd.DataFrame:
    """Use type-specific solar where present, otherwise generic solar."""
    missing_generic = [column for column in generic if column not in specific]
    combined = pd.concat([specific, generic[missing_generic]], axis=1)
    combined = combined.rename(columns=_NODE_MAP)
    if combined.columns.duplicated().any():
        duplicates = sorted(set(combined.columns[combined.columns.duplicated()]))
        raise ValueError(f"Duplicate solar bus columns after node remapping: {duplicates}")
    return combined


def _load_pecd_profiles(
    tyndp_dir: Path,
    climate_year: int,
    offshore_cap_df: pd.DataFrame,
) -> tuple[dict[str, pd.DataFrame], str]:
    """Load all PECD profiles from a complete parquet bundle or raw CSVs."""
    preprocessed_dir = tyndp_dir / "preprocessed"
    paths = {
        key: preprocessed_dir / filename
        for key, filename in _PECD_PARQUET_FILES.items()
    }
    present = {key for key, path in paths.items() if path.is_file()}

    if present and len(present) != len(paths):
        missing = [paths[key].name for key in paths if key not in present]
        raise FileNotFoundError(
            "Incomplete preprocessed PECD parquet bundle; missing: "
            + ", ".join(missing)
        )

    if len(present) == len(paths):
        onshore = _read_pecd_parquet(paths["wind_onshore"], climate_year)
        offshore_raw = _read_pecd_parquet(paths["wind_offshore"], climate_year)
        generic = _read_pecd_parquet(paths["solar_generic"], climate_year)
        utility = _read_pecd_parquet(paths["solar_utility"], climate_year)
        rooftop = _read_pecd_parquet(paths["solar_rooftop"], climate_year)

        onshore = onshore.rename(columns=_NODE_MAP)
        if onshore.columns.duplicated().any():
            duplicates = sorted(set(onshore.columns[onshore.columns.duplicated()]))
            raise ValueError(
                f"Duplicate onshore bus columns after node remapping: {duplicates}"
            )

        return (
            {
                "wind_onshore": onshore,
                "wind_offshore": _aggregate_pecd_offshore_parquet(
                    offshore_raw, offshore_cap_df
                ),
                "solar_utility": _merge_pecd_solar(utility, generic),
                "solar_rooftop": _merge_pecd_solar(rooftop, generic),
            },
            "preprocessed parquet",
        )

    pecd_dir = tyndp_dir / "PECD 2030"
    return (
        {
            "wind_onshore": _load_pecd_onshore(pecd_dir, climate_year),
            "wind_offshore": _load_pecd_offshore(
                pecd_dir, climate_year, offshore_cap_df
            ),
            "solar_utility": _load_pecd_solar(
                pecd_dir, climate_year, _PECD_UTILITY_SLUG
            ),
            "solar_rooftop": _load_pecd_solar(
                pecd_dir, climate_year, _PECD_ROOFTOP_SLUG
            ),
        },
        "raw CSV",
    )


# ===========================================================================
# Demand loader
# ===========================================================================


def _load_demand(tyndp_dir: Path, climate_year: int) -> pd.DataFrame:
    """Hourly electricity demand for one climate year.

    Uses preprocessed parquet if available (see scenarios/preprocess_xlsx.py),
    otherwise falls back to reading the original XLSX.

    Returns DataFrame: index = 8760 hourly 2030 timestamps, columns = bus codes.
    Units: MWh/h.
    """
    parquet_path = tyndp_dir / "preprocessed" / "demand_profiles.parquet"
    idx = pd.date_range("2030-01-01", periods=8760, freq="h")

    if parquet_path.exists():
        df = pd.read_parquet(parquet_path)
        demand = df.loc[climate_year].sort_index()
        demand.index = idx
        return demand.rename(columns=_NODE_MAP)

    # Fallback: read from XLSX
    xl_path = (
        tyndp_dir
        / "Demand Profiles"
        / "NT"
        / "Electricity demand profiles"
        / "2030_National Trends.xlsx"
    )
    xl = pd.ExcelFile(xl_path, engine="openpyxl")
    bus_data: dict[str, np.ndarray] = {}

    for sheet in xl.sheet_names:
        df = pd.read_excel(xl, sheet_name=sheet, header=7, engine="openpyxl")
        cy_col = climate_year if climate_year in df.columns else float(climate_year)
        if cy_col not in df.columns:
            continue
        values = pd.to_numeric(df[cy_col], errors="coerce").fillna(0.0).values
        if len(values) > 8760:
            values = values[:8760]
        elif len(values) < 8760:
            values = np.pad(values, (0, 8760 - len(values)))
        bus = _NODE_MAP.get(sheet, sheet)
        bus_data[bus] = values

    demand = pd.DataFrame(bus_data)
    demand.index = idx
    return demand


# ===========================================================================
# Hydro inflow loaders
# ===========================================================================


def _daily_to_hourly(day_series: np.ndarray) -> np.ndarray:
    """Repeat each daily value 24 times and convert GWh/day → MWh/h.

    Raw TYNDP Excel inflows are in GWh/day.  To obtain MWh/h (= MW equivalent)
    we multiply by 1 000 MWh/GWh and divide by 24 h/day.
    """
    return np.repeat(np.asarray(day_series, dtype=float)[:365], 24) * (1_000.0 / 24.0)


def _weekly_to_hourly(week_series: np.ndarray) -> np.ndarray:
    """Repeat each weekly value 168 times, pad to 8760 h, and convert GWh/week → MWh/h.

    Raw TYNDP Excel inflows are in GWh/week.  To obtain MWh/h (= MW equivalent)
    we multiply by 1 000 MWh/GWh and divide by 168 h/week.
    """
    week_arr = np.asarray(week_series, dtype=float)[:52]
    hourly = np.repeat(week_arr, 168)
    pad = 8760 - len(hourly)
    if pad > 0:
        hourly = np.append(hourly, np.repeat(week_arr[-1], pad))
    return hourly[:8760] * (1_000.0 / 168.0)


def _load_hydro_for_bus(
    xl: pd.ExcelFile, climate_year: int
) -> dict[str, np.ndarray | None]:
    """Extract CY-specific hourly inflow arrays for all hydro types from one Excel file.

    Reads these sheets (if present):
        Run of River - Year Dependent   (daily, GWh/day)
        Reservoir - Year Dependent      (weekly, GWh/week)
        Pondage - Year Dependent        (daily, GWh/day)
        PS Open - Year Dependent        (weekly, GWh/week)

    Returns dict with keys: ror, reservoir, pondage, ps_open.
    Values are 8760-element float arrays, or None if the sheet/column is absent.
    """
    result: dict[str, np.ndarray | None] = {
        "ror": None,
        "reservoir": None,
        "pondage": None,
        "ps_open": None,
    }

    sheet_configs = {
        "ror": ("Run of River - Year Dependent", "Energy Inflow [GWh/day]", "day"),
        "reservoir": ("Reservoir - Year Dependent", "Energy Inflow [GWh/week]", "week"),
        "pondage": ("Pondage - Year Dependent", "Energy Inflow [GWh/day]", "day"),
        "ps_open": ("PS Open - Year Dependent", "Energy Inflow [GWh/week]", "week"),
    }

    for key, (sheet, var_label, time_res) in sheet_configs.items():
        if sheet not in xl.sheet_names:
            continue
        try:
            df = pd.read_excel(xl, sheet_name=sheet, header=1, engine="openpyxl")
        except Exception:
            continue

        cy_col = climate_year if climate_year in df.columns else float(climate_year)
        if cy_col not in df.columns:
            continue

        inflow_rows = df[df["Variable"].astype(str).str.strip() == var_label]
        if inflow_rows.empty:
            continue

        values = pd.to_numeric(inflow_rows[cy_col], errors="coerce").fillna(0.0).values
        result[key] = (
            _daily_to_hourly(values) if time_res == "day" else _weekly_to_hourly(values)
        )

    return result


def _load_hydro(tyndp_dir: Path, climate_year: int) -> dict[str, pd.DataFrame]:
    """Hydro inflow time series for all buses, one climate year.

    Uses preprocessed parquet if available (see scenarios/preprocess_xlsx.py),
    otherwise falls back to reading individual XLSX files.

    Returns dict with keys: hydro_ror, hydro_reservoir, hydro_pondage, hydro_ps_open.
    Each value is a DataFrame with 2030 datetime index and bus columns.
    """
    idx = pd.date_range("2030-01-01", periods=8760, freq="h")
    parquet_path = tyndp_dir / "preprocessed" / "hydro_inflows.parquet"

    if parquet_path.exists():
        df = pd.read_parquet(parquet_path)
        cy_df = df.loc[climate_year].sort_index()
        cy_df.index = idx

        def _extract(hydro_type: str) -> pd.DataFrame:
            if hydro_type in cy_df.columns.get_level_values("hydro_type"):
                return cy_df.xs(hydro_type, level="hydro_type", axis=1).rename(columns=_NODE_MAP)
            return pd.DataFrame(index=idx)

        return {
            "hydro_ror":        _extract("ror"),
            "hydro_reservoir":  _extract("reservoir"),
            "hydro_pondage":    _extract("pondage"),
            "hydro_ps_open":    _extract("ps_open"),
        }

    # Fallback: read from individual XLSX files
    hydro_dir = tyndp_dir / "Hydro Inflows" / "2030"
    bus_results: dict[str, dict[str, np.ndarray | None]] = {}

    for xl_path in sorted(hydro_dir.glob("PEMMDB_*_Hydro_Inflows_2030.xlsx")):
        m = re.match(r"PEMMDB_([A-Z0-9]+)_Hydro_Inflows_2030\.xlsx", xl_path.name)
        if not m:
            continue
        bus = m.group(1)
        bus_results[bus] = _load_hydro_for_bus(
            pd.ExcelFile(xl_path, engine="openpyxl"), climate_year
        )

    def _df(key: str) -> pd.DataFrame:
        cols = {b: r[key] for b, r in bus_results.items() if r[key] is not None}
        df = pd.DataFrame(cols, index=idx) if cols else pd.DataFrame(index=idx)
        return df.rename(columns=_NODE_MAP)

    return {
        "hydro_ror":        _df("ror"),
        "hydro_reservoir":  _df("reservoir"),
        "hydro_pondage":    _df("pondage"),
        "hydro_ps_open":    _df("ps_open"),
    }


# ===========================================================================
# Gas / coal / CO₂ price adjustment
# ===========================================================================


def _apply_gas_co2_prices(
    tech_df: pd.DataFrame,
    gas_price: float | None,
    co2_price: float | None,
    coal_price: float | None = None,
) -> pd.DataFrame:
    """Recompute marginal costs for new gas, coal, and CO₂ prices.

    Uses the same formula as create_technologies_costs_dataset.ipynb:
        MC = VOM + (fuel_price / efficiency) + (co2_tco2_mwh / efficiency) × co2_price

    Gas rows (fuel_type in {'gas', 'gas-ccs'}):
        Recomputed fully when gas_price is provided, using the new gas_price
        in place of fuel_price_eur_mwh.

    Coal rows (fuel_type == 'coal'):
        Recomputed fully when coal_price is provided, using the new coal_price
        in place of fuel_price_eur_mwh.

    Other thermal rows with co2_tco2_mwh > 0:
        Recomputed fully when co2_price is provided, keeping their original
        fuel_price_eur_mwh from the CSV.

    Required columns: efficiency, vom_eur_mwh, fuel_price_eur_mwh,
                      co2_tco2_mwh, fuel_type.
    """
    tech = tech_df.copy()

    required = {
        "efficiency",
        "vom_eur_mwh",
        "fuel_price_eur_mwh",
        "co2_tco2_mwh",
        "fuel_type",
    }
    if not required.issubset(tech.columns):
        return tech

    # Rows with valid efficiency and non-zero CO₂ emissions
    valid_eff = tech["efficiency"].notna() & (tech["efficiency"] > 0)
    has_co2 = tech["co2_tco2_mwh"].notna() & (tech["co2_tco2_mwh"] > 0)
    thermal = valid_eff & has_co2

    fuel_type = tech["fuel_type"].astype(str).str.lower()
    gas_mask = fuel_type.isin(["gas", "gas-ccs"])
    coal_mask = fuel_type.eq("coal")

    # Gas rows: recompute with new gas_price
    if gas_price is not None:
        update = gas_mask & thermal
        cp = co2_price if co2_price is not None else 0.0
        eff = tech.loc[update, "efficiency"]
        tech.loc[update, "marginal_cost_eur_mwh"] = (
            tech.loc[update, "vom_eur_mwh"].fillna(0.0)
            + gas_price / eff
            + tech.loc[update, "co2_tco2_mwh"] / eff * cp
        )

    # Coal rows: recompute with new coal_price
    if coal_price is not None:
        update = coal_mask & thermal
        cp = co2_price if co2_price is not None else 0.0
        eff = tech.loc[update, "efficiency"]
        tech.loc[update, "marginal_cost_eur_mwh"] = (
            tech.loc[update, "vom_eur_mwh"].fillna(0.0)
            + coal_price / eff
            + tech.loc[update, "co2_tco2_mwh"] / eff * cp
        )

    # Non-gas thermal rows: recompute with new co2_price, keep fuel price
    if co2_price is not None:
        update = ~gas_mask & thermal
        if coal_price is not None:
            update &= ~coal_mask
        eff = tech.loc[update, "efficiency"]
        tech.loc[update, "marginal_cost_eur_mwh"] = (
            tech.loc[update, "vom_eur_mwh"].fillna(0.0)
            + tech.loc[update, "fuel_price_eur_mwh"].fillna(0.0) / eff
            + tech.loc[update, "co2_tco2_mwh"] / eff * co2_price
        )

    return tech


# ===========================================================================
# Public API
# ===========================================================================


def load_network_data(
    data_dir: Path | str = "data/open-tyndp",
    tyndp_dir: Path | str = "data/tyndp2024",
    climate_year: int = 2009,
    gas_price: float | None = None,
    co2_price: float | None = None,
    coal_price: float | None = None,
) -> dict:
    """Load all input data required to build the TYNDP 2030 PyPSA network.

    Returns a dict of DataFrames that is passed directly to build_network().

    Parameters
    ----------
    data_dir :
        Path to the pre-processed open-tyndp directory.  Must contain:
        buses.csv, links.csv, pemmdb_capacities_2030_grouped.csv,
        technologies_2030.csv, offshore_wind_capacity_by_bus.csv,
        nuclear_p_max_pu_2030.csv, other_res_p_max_pu.csv,
        dsr_pemmdb.csv, dsr_p_max_pu_timeseries.csv.
    tyndp_dir :
        Path to the raw TYNDP 2024 data directory.  Must contain:
        'PECD 2030/', 'Demand Profiles/NT/Electricity demand profiles/',
        'Hydro Inflows/2030/'.
    climate_year :
        Climate year to extract from all CY-dependent raw files.
        Valid range: 1982-2017 for PECD and hydro, 1982-2019 for demand.
    gas_price :
        Natural gas price in EUR/MWh_th.  If provided, marginal costs of
        gas-based generators are recomputed as:
            MC = gas_price / efficiency + co2_tco2_mwh * co2_price
        If None, the values in technologies_2030.csv are used as-is.
    co2_price :
        CO₂ allowance price in EUR/tCO₂.  Applied together with gas_price for
        gas rows.  Also corrects other thermal rows via delta adjustment when
        the 'co2_price_eur_tco2' reference column is present.
    coal_price :
        Coal price in EUR/MWh_th.  If provided, marginal costs of all
        coal-fuelled generators, including CHP and CCS, are recomputed using
        the same formula as gas-based generators.  If None, coal prices from
        technologies_2030.csv are retained.

    Returns
    -------
    dict
        Keys:
            buses               DataFrame   topology buses
            links               DataFrame   topology DC links
            capacities          DataFrame   PEMMDB generator/storage capacities
            technologies        DataFrame   carrier parameters and marginal costs
            offshore_cap        Series      existing offshore MW by bus
            offshore_cap_df     DataFrame   same with zone breakdown column
            nuclear_profiles    DataFrame   hourly nuclear p_max_pu (fixed, non-CY)
            other_res_pmax      DataFrame   hourly other-res p_max_pu (fixed, non-CY)
            dsr_static          DataFrame   DSR band capacities and prices
            dsr_ts              DataFrame   DSR availability time series
            wind_onshore        DataFrame   hourly onshore wind CF for climate_year
            wind_offshore       DataFrame   hourly offshore wind CF for climate_year
            solar_utility       DataFrame   hourly utility solar CF for climate_year
            solar_rooftop       DataFrame   hourly rooftop solar CF for climate_year
            electricity_demand  DataFrame   hourly demand in MWh/h for climate_year
            hydro_ror           DataFrame   hourly RoR inflow for climate_year
            hydro_reservoir     DataFrame   hourly reservoir inflow for climate_year
            hydro_pondage       DataFrame   hourly pondage inflow for climate_year
            hydro_ps_open       DataFrame   hourly PS Open inflow for climate_year
    """
    data_dir = Path(data_dir)
    tyndp_dir = Path(tyndp_dir)

    print(f"Loading network data: data_dir={data_dir}, tyndp_dir={tyndp_dir}")
    print(f"  climate_year={climate_year}", end="")
    if gas_price is not None or co2_price is not None or coal_price is not None:
        print(
            f", gas_price={gas_price}, co2_price={co2_price}, "
            f"coal_price={coal_price}",
            end="",
        )
    print()

    # Fixed files
    print("[1/4] Loading fixed data...")
    data = _load_fixed(data_dir)

    if gas_price is not None or co2_price is not None or coal_price is not None:
        data["technologies"] = _apply_gas_co2_prices(
            data["technologies"], gas_price, co2_price, coal_price
        )

    # Pre-filter DSR by climate year and store CY label for the builder
    data["climate_year"] = climate_year
    if "Climate year start" in data["dsr_static"].columns:
        data["dsr_static"] = data["dsr_static"][
            (data["dsr_static"]["Climate year start"] <= climate_year)
            & (data["dsr_static"]["Climate year end"] >= climate_year)
        ].copy()

    # PECD profiles
    print(f"[2/4] Loading PECD profiles (CY={climate_year})...")
    pecd_profiles, pecd_source = _load_pecd_profiles(
        tyndp_dir, climate_year, data["offshore_cap_df"]
    )
    data.update(pecd_profiles)
    print(
        f"  Source: {pecd_source}; "
        f"Onshore: {data['wind_onshore'].shape[1]} buses, "
        f"Offshore: {data['wind_offshore'].shape[1]} buses, "
        f"Utility: {data['solar_utility'].shape[1]} buses, "
        f"Rooftop: {data['solar_rooftop'].shape[1]} buses"
    )

    # Demand
    print(f"[3/4] Loading demand (CY={climate_year})...")
    data["electricity_demand"] = _load_demand(tyndp_dir, climate_year)
    print(f"  {data['electricity_demand'].shape[1]} buses")

    # Hydro inflows
    print(f"[4/4] Loading hydro inflows (CY={climate_year})...")
    hydro = _load_hydro(tyndp_dir, climate_year)
    data["hydro_ror"] = hydro["hydro_ror"]
    data["hydro_reservoir"] = hydro["hydro_reservoir"]
    data["hydro_pondage"] = hydro["hydro_pondage"]
    data["hydro_ps_open"] = hydro["hydro_ps_open"]
    print(
        f"  RoR: {data['hydro_ror'].shape[1]} buses, "
        f"Reservoir: {data['hydro_reservoir'].shape[1]} buses, "
        f"Pondage: {data['hydro_pondage'].shape[1]} buses, "
        f"PS Open: {data['hydro_ps_open'].shape[1]} buses"
    )

    print("Data loading complete.")
    return data
