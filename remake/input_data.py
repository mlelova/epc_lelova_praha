"""CSV/Excel-only base input loading for :mod:`remake`.

The original scenario pipeline can continue to use its preprocessed Parquet
bundle.  The remake deliberately consumes the smaller, one-climate-year
tables in ``data/open-tyndp`` so it can later be separated from that pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .errors import OverrideValidationError


HOURS_PER_YEAR = 8760
MODEL_SNAPSHOTS = pd.date_range("2030-01-01", periods=HOURS_PER_YEAR, freq="h")
SUPPORTED_SUFFIXES = (".csv", ".xlsx", ".xlsm")


@dataclass(frozen=True)
class HourlyInput:
    filename: str
    label: str
    climate_dependent: bool = False
    minimum: float | None = None
    maximum: float | None = None


HOURLY_INPUTS = {
    "nuclear_profiles": HourlyInput(
        "nuclear_p_max_pu_2030.csv", "nuclear profiles", minimum=0.0, maximum=1.0
    ),
    "other_res_pmax": HourlyInput(
        "other_res_p_max_pu.csv", "other-res profiles", minimum=0.0, maximum=1.0
    ),
    "dsr_ts": HourlyInput(
        "dsr_p_max_pu_timeseries.csv", "DSR profiles", minimum=0.0, maximum=1.0
    ),
    "wind_onshore": HourlyInput(
        "pecd_data_Wind_Onshore_2030.csv",
        "onshore-wind profiles",
        climate_dependent=True,
        minimum=0.0,
        maximum=1.0,
    ),
    "wind_offshore": HourlyInput(
        "pecd_data_Wind_Offshore_2030_mapped.csv",
        "offshore-wind profiles",
        climate_dependent=True,
        minimum=0.0,
        maximum=1.0,
    ),
    "solar_utility": HourlyInput(
        "pecd_data_LFSolarPVUtility_2030.csv",
        "utility-solar profiles",
        climate_dependent=True,
        minimum=0.0,
        maximum=1.0,
    ),
    "solar_rooftop": HourlyInput(
        "pecd_data_LFSolarPVRooftop_2030.csv",
        "rooftop-solar profiles",
        climate_dependent=True,
        minimum=0.0,
        maximum=1.0,
    ),
    "electricity_demand": HourlyInput(
        "electricity_demand_2030.csv",
        "electricity-demand profiles",
        climate_dependent=True,
        minimum=0.0,
    ),
    "hydro_ror": HourlyInput(
        "hydro_inflows_tyndp_Run_of_River_2030.csv",
        "run-of-river inflows",
        climate_dependent=True,
        minimum=0.0,
    ),
    "hydro_reservoir": HourlyInput(
        "hydro_inflows_tyndp_Reservoir_2030.csv",
        "reservoir inflows",
        climate_dependent=True,
        minimum=0.0,
    ),
    "hydro_pondage": HourlyInput(
        "hydro_inflows_tyndp_Pondage_2030.csv",
        "pondage inflows",
        climate_dependent=True,
        minimum=0.0,
    ),
    "hydro_ps_open": HourlyInput(
        "hydro_inflows_tyndp_PS_Open_2030.csv",
        "open pumped-storage inflows",
        climate_dependent=True,
        minimum=0.0,
    ),
}


def read_table(path: Path | str, label: str, **kwargs) -> pd.DataFrame:
    """Read one CSV or modern Excel table with a consistent error message."""
    path = Path(path)
    if not path.is_file():
        raise OverrideValidationError(f"{label} file does not exist: {path}")

    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            return pd.read_csv(path, **kwargs)
        if suffix in {".xlsx", ".xlsm"}:
            return pd.read_excel(path, engine="openpyxl", **kwargs)
    except Exception as exc:
        raise OverrideValidationError(
            f"Could not read {label} file {path}: {exc}"
        ) from exc

    supported = ", ".join(SUPPORTED_SUFFIXES)
    raise OverrideValidationError(
        f"Unsupported {label} format {suffix!r}; use one of: {supported}"
    )


def resolve_table(data_dir: Path | str, filename: str, label: str) -> Path:
    """Resolve a named base input, allowing its CSV to be replaced by Excel."""
    data_dir = Path(data_dir)
    requested = data_dir / filename
    stem = requested.with_suffix("")
    candidates = [stem.with_suffix(suffix) for suffix in SUPPORTED_SUFFIXES]
    present = [candidate for candidate in candidates if candidate.is_file()]
    if not present:
        expected = ", ".join(candidate.name for candidate in candidates)
        raise OverrideValidationError(
            f"Missing {label} in {data_dir}; expected one of: {expected}"
        )
    if len(present) > 1:
        names = ", ".join(path.name for path in present)
        raise OverrideValidationError(
            f"Ambiguous {label} in {data_dir}; keep only one of: {names}"
        )
    return present[0]


def _named_table(
    data_dir: Path, filename: str, label: str, **kwargs
) -> pd.DataFrame:
    return read_table(resolve_table(data_dir, filename, label), label, **kwargs)


def load_fixed_inputs(data_dir: Path | str) -> dict:
    """Load the non-hourly base tables required by the trusted builder."""
    data_dir = Path(data_dir)
    technologies = _named_table(
        data_dir, "technologies_2030.csv", "technology base"
    )
    capacities = _named_table(
        data_dir, "pemmdb_capacities_2030_grouped.csv", "capacity base"
    )
    capacities = capacities.drop(columns=["efficiency"], errors="ignore")
    technology_metadata = technologies[
        ["index_carrier", "pypsa_carrier", "efficiency"]
    ].drop_duplicates("index_carrier")
    capacities = capacities.merge(
        technology_metadata, on="index_carrier", how="left"
    )

    links_path = resolve_table(data_dir, "links.csv", "link base")
    if links_path.suffix.lower() == ".csv":
        # The source CSV contains a single-quoted WKT geometry with a comma.
        # Preserve the parser used by the trusted scenario loader.
        links = read_table(
            links_path,
            "link base",
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
        )
    else:
        links = read_table(links_path, "link base", index_col=0)

    offshore = _named_table(
        data_dir, "offshore_wind_capacity_by_bus.csv", "offshore-capacity base"
    )
    return {
        "buses": _named_table(data_dir, "buses.csv", "bus base"),
        "links": links,
        "capacities": capacities,
        "technologies": technologies,
        "offshore_cap": offshore.set_index("bus")["total_existing_mw"],
        "offshore_cap_df": offshore,
        "dsr_static": _named_table(data_dir, "dsr_pemmdb.csv", "DSR base"),
    }


def load_hourly_input(
    data_dir: Path | str,
    key: str,
    climate_year: int,
) -> pd.DataFrame:
    """Load and validate one 8,760-row base profile from CSV or Excel."""
    if key not in HOURLY_INPUTS:
        raise KeyError(f"Unknown hourly input key: {key}")
    spec = HOURLY_INPUTS[key]
    path = resolve_table(data_dir, spec.filename, spec.label)
    frame = read_table(path, spec.label, index_col=0)
    if frame.empty or frame.shape[1] == 0:
        raise OverrideValidationError(f"{spec.label} file is empty: {path}")

    parsed_index = pd.to_datetime(frame.index, errors="coerce")
    if parsed_index.isna().any():
        raise OverrideValidationError(f"{spec.label} contains invalid timestamps")
    frame.index = pd.DatetimeIndex(parsed_index)
    if len(frame) != HOURS_PER_YEAR:
        raise OverrideValidationError(
            f"{spec.label} must contain exactly {HOURS_PER_YEAR} hourly rows; "
            f"found {len(frame)}"
        )
    if frame.index.duplicated().any() or not frame.index.is_monotonic_increasing:
        raise OverrideValidationError(
            f"{spec.label} timestamps must be unique and increasing"
        )
    expected = pd.date_range(frame.index[0], periods=HOURS_PER_YEAR, freq="h")
    if not frame.index.equals(expected):
        raise OverrideValidationError(
            f"{spec.label} timestamps must be contiguous hourly values"
        )
    if spec.climate_dependent and set(frame.index.year) != {climate_year}:
        source_years = ", ".join(str(year) for year in sorted(set(frame.index.year)))
        raise OverrideValidationError(
            f"{spec.label} contains climate year(s) {source_years}, "
            f"not requested climate year {climate_year}"
        )

    numeric = frame.apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise OverrideValidationError(
            f"{spec.label} contains missing or non-numeric values"
        )
    if spec.minimum is not None and (numeric < spec.minimum - 1e-6).any().any():
        raise OverrideValidationError(
            f"{spec.label} values must be at least {spec.minimum}"
        )
    if spec.maximum is not None and (numeric > spec.maximum + 1e-6).any().any():
        raise OverrideValidationError(
            f"{spec.label} values must be at most {spec.maximum}"
        )

    if spec.minimum == 0.0 and spec.maximum == 1.0:
        numeric = numeric.clip(0.0, 1.0)
    numeric.index = MODEL_SNAPSHOTS
    return numeric


def apply_fuel_prices(
    technologies: pd.DataFrame,
    gas_price: float | None,
    co2_price: float | None,
    coal_price: float | None,
) -> pd.DataFrame:
    """Apply the remake's gas, coal, and carbon price assumptions."""
    tech = technologies.copy()
    required = {
        "efficiency",
        "vom_eur_mwh",
        "fuel_price_eur_mwh",
        "co2_tco2_mwh",
        "fuel_type",
    }
    if not required.issubset(tech.columns):
        return tech

    valid_efficiency = tech["efficiency"].notna() & tech["efficiency"].gt(0)
    emits_co2 = tech["co2_tco2_mwh"].notna() & tech["co2_tco2_mwh"].gt(0)
    thermal = valid_efficiency & emits_co2
    fuel_type = tech["fuel_type"].astype(str).str.lower()
    gas = fuel_type.isin(["gas", "gas-ccs"])
    coal = fuel_type.eq("coal")

    def update_cost(mask: pd.Series, fuel_price: float | pd.Series, carbon: float) -> None:
        efficiency = tech.loc[mask, "efficiency"]
        tech.loc[mask, "marginal_cost_eur_mwh"] = (
            tech.loc[mask, "vom_eur_mwh"].fillna(0.0)
            + fuel_price / efficiency
            + tech.loc[mask, "co2_tco2_mwh"] / efficiency * carbon
        )

    if gas_price is not None:
        update_cost(gas & thermal, gas_price, co2_price or 0.0)
    if coal_price is not None:
        update_cost(coal & thermal, coal_price, co2_price or 0.0)
    if co2_price is not None:
        remaining = ~gas & thermal
        if coal_price is not None:
            remaining &= ~coal
        update_cost(
            remaining,
            tech.loc[remaining, "fuel_price_eur_mwh"].fillna(0.0),
            co2_price,
        )
    return tech
