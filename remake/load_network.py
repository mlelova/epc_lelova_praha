"""Load the scenario-engine inputs and apply strict company-data overrides."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .errors import OverrideValidationError
from .input_data import (
    HOURS_PER_YEAR,
    MODEL_SNAPSHOTS,
    apply_fuel_prices,
    load_fixed_inputs,
    load_hourly_input,
    read_table,
)


PROFILE_KEYS = (
    "nuclear_profiles",
    "other_res_pmax",
    "dsr_ts",
    "wind_onshore",
    "wind_offshore",
    "solar_utility",
    "solar_rooftop",
    "electricity_demand",
    "hydro_ror",
    "hydro_reservoir",
    "hydro_pondage",
    "hydro_ps_open",
)
VRE_CARRIERS = {
    "wind_onshore": "onwind",
    "wind_offshore": "offwind",
    "solar_utility": "solar-pv-utility",
    "solar_rooftop": "solar-pv-rooftop",
}


def _read_table(path: Path | str, label: str) -> pd.DataFrame:
    return read_table(path, label)


def _require_columns(df: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = set(columns) - set(df.columns)
    if missing:
        raise OverrideValidationError(
            f"{label} is missing required column(s): {', '.join(sorted(missing))}"
        )


def _numeric(df: pd.DataFrame, columns: Iterable[str], label: str) -> pd.DataFrame:
    result = df.copy()
    for column in columns:
        converted = pd.to_numeric(result[column], errors="coerce")
        invalid = converted.isna() & result[column].notna()
        if invalid.any():
            rows = (invalid[invalid].index + 2).tolist()[:5]
            raise OverrideValidationError(
                f"{label}.{column} contains non-numeric values at input row(s) {rows}"
            )
        result[column] = converted
    return result


def _bus_ids(data: dict) -> set[str]:
    _require_columns(data["buses"], ["bus_id"], "base buses")
    return set(data["buses"]["bus_id"].astype(str))


def _validate_known(values: Iterable[object], known: set[str], label: str) -> None:
    supplied = {str(value) for value in values}
    unknown = sorted(supplied - known)
    if unknown:
        shown = ", ".join(unknown[:10])
        suffix = " ..." if len(unknown) > 10 else ""
        raise OverrideValidationError(f"Unknown {label}: {shown}{suffix}")


def apply_capacity_override(data: dict, path: Path | str) -> None:
    """Update existing capacity rows keyed by ``bus,index_carrier``.

    The preferred power column is ``p_nom_mw``. ``p_nom`` is accepted for
    compatibility with the scenario table. Energy may be supplied as
    ``e_nom_mwh`` or ``e_nom``.
    """
    override = _read_table(path, "capacity override")
    _require_columns(override, ["bus", "index_carrier"], "capacity override")
    if {"p_nom_mw", "p_nom"}.issubset(override.columns) or {
        "e_nom_mwh",
        "e_nom",
    }.issubset(override.columns):
        raise OverrideValidationError(
            "capacity override must not contain both a unit-explicit column and its alias"
        )
    rename = {}
    if "p_nom_mw" in override.columns:
        rename["p_nom_mw"] = "p_nom"
    if "e_nom_mwh" in override.columns:
        rename["e_nom_mwh"] = "e_nom"
    override = override.rename(columns=rename)
    value_columns = [column for column in ("p_nom", "e_nom") if column in override]
    if not value_columns:
        raise OverrideValidationError(
            "capacity override requires p_nom_mw (MW) or e_nom_mwh (MWh)"
        )
    if override.duplicated(["bus", "index_carrier"]).any():
        raise OverrideValidationError(
            "capacity override contains duplicate bus,index_carrier keys"
        )
    override = _numeric(override, value_columns, "capacity override")
    if override[value_columns].isna().any().any():
        raise OverrideValidationError("capacity override values may not be empty")
    if override[value_columns].lt(0).any().any():
        raise OverrideValidationError("capacity override values must be non-negative")
    _validate_known(override["bus"], _bus_ids(data), "capacity override bus(es)")

    capacities = data["capacities"].copy()
    base_keys = set(
        zip(capacities["bus"].astype(str), capacities["index_carrier"].astype(str))
    )
    keys = list(zip(override["bus"].astype(str), override["index_carrier"].astype(str)))
    missing = sorted(set(keys) - base_keys)
    if missing:
        shown = ", ".join(f"{bus}/{carrier}" for bus, carrier in missing[:10])
        raise OverrideValidationError(f"Unknown capacity key(s): {shown}")

    indexed = capacities.set_index(["bus", "index_carrier"])
    updates = override.set_index(["bus", "index_carrier"])
    for column in value_columns:
        values = updates[column].copy()
        if column == "p_nom":
            # Company files express physical pump capacity as positive MW;
            # the scenario engine uses a negative sign to encode charging.
            pump = values.index.get_level_values("index_carrier").astype(str).str.endswith(
                "-pump"
            )
            values.loc[pump] = -values.loc[pump]
        indexed.loc[updates.index, column] = values.to_numpy()
    data["capacities"] = indexed.reset_index()

    if "p_nom" in value_columns and "offshore_cap" in data:
        offshore = updates.reset_index()
        offshore = offshore[offshore["index_carrier"].astype(str).eq("offwind")]
        for _, row in offshore.iterrows():
            data["offshore_cap"].loc[str(row["bus"])] = float(row["p_nom"])


def apply_technology_override(data: dict, path: Path | str) -> None:
    """Update technology fields by either index_carrier or pypsa_carrier."""
    override = _read_table(path, "technology override")
    keys = [key for key in ("index_carrier", "pypsa_carrier") if key in override]
    if len(keys) != 1:
        raise OverrideValidationError(
            "technology override requires exactly one key column: "
            "index_carrier or pypsa_carrier"
        )
    key = keys[0]
    if override[key].duplicated().any():
        raise OverrideValidationError(f"technology override contains duplicate {key} keys")

    technologies = data["technologies"].copy()
    _validate_known(override[key], set(technologies[key].astype(str)), f"technology {key}(s)")
    value_columns = [column for column in override.columns if column != key]
    if not value_columns:
        raise OverrideValidationError("technology override contains no values to update")
    unknown_columns = sorted(set(value_columns) - set(technologies.columns))
    if unknown_columns:
        raise OverrideValidationError(
            f"Unknown technology field(s): {', '.join(unknown_columns)}"
        )

    numeric_columns = [
        column
        for column in value_columns
        if pd.api.types.is_numeric_dtype(technologies[column])
    ]
    override = _numeric(override, numeric_columns, "technology override")
    for _, row in override.iterrows():
        mask = technologies[key].astype(str).eq(str(row[key]))
        for column in value_columns:
            if pd.notna(row[column]):
                technologies.loc[mask, column] = row[column]
    data["technologies"] = technologies

    # The base loader copies these fields onto capacities before overrides.
    # Keep that denormalized table in sync so storage assembly sees changes.
    for column in ("pypsa_carrier", "efficiency"):
        if column in value_columns and column in data["capacities"]:
            mapping = technologies.drop_duplicates("index_carrier").set_index(
                "index_carrier"
            )[column]
            data["capacities"][column] = data["capacities"]["index_carrier"].map(
                mapping
            )


def read_battery_override(
    path: Path | str,
    buses: set[str],
    supported_buses: set[str] | None = None,
) -> pd.DataFrame:
    override = _read_table(path, "battery override")
    _require_columns(override, ["bus", "p_nom_mw", "duration_h"], "battery override")
    if override["bus"].duplicated().any():
        raise OverrideValidationError("battery override contains duplicate bus keys")
    override = _numeric(override, ["p_nom_mw", "duration_h"], "battery override")
    if override[["p_nom_mw", "duration_h"]].isna().any().any():
        raise OverrideValidationError("battery override values may not be empty")
    _validate_known(override["bus"], buses, "battery override bus(es)")
    if supported_buses is not None:
        _validate_known(
            override["bus"], supported_buses, "battery-capable override bus(es)"
        )
    if override["p_nom_mw"].lt(0).any() or override["duration_h"].le(0).any():
        raise OverrideValidationError(
            "battery p_nom_mw must be non-negative and duration_h must be positive"
        )
    return override[["bus", "p_nom_mw", "duration_h"]].copy()


def read_ntc_override(path: Path | str, links: set[str]) -> pd.DataFrame:
    override = _read_table(path, "NTC override")
    _require_columns(override, ["link_id", "p_nom"], "NTC override")
    if override["link_id"].duplicated().any():
        raise OverrideValidationError("NTC override contains duplicate link_id keys")
    override = _numeric(override, ["p_nom"], "NTC override")
    if override["p_nom"].isna().any():
        raise OverrideValidationError("NTC override values may not be empty")
    _validate_known(override["link_id"], links, "NTC override link(s)")
    if override["p_nom"].lt(0).any():
        raise OverrideValidationError("NTC p_nom values must be non-negative MW")
    return override[["link_id", "p_nom"]].copy()


def read_profile_override(
    path: Path | str,
    label: str,
    buses: set[str],
    value_name: str = "p_max_pu",
    bounded: bool = True,
) -> pd.DataFrame:
    """Read either a wide timestamp+bus table or long timestamp,bus,value table."""
    raw = _read_table(path, label)
    timestamp_candidates = [
        name for name in ("snapshot", "timestamp", "datetime", "time") if name in raw
    ]
    timestamp_column = timestamp_candidates[0] if timestamp_candidates else raw.columns[0]

    if {"bus", value_name}.issubset(raw.columns):
        parsed = pd.to_datetime(raw[timestamp_column], errors="coerce")
        if parsed.isna().any():
            raise OverrideValidationError(f"{label} contains invalid timestamps")
        long = raw.assign(_snapshot=parsed)
        if long.duplicated(["_snapshot", "bus"]).any():
            raise OverrideValidationError(f"{label} contains duplicate timestamp,bus rows")
        profile = long.pivot(index="_snapshot", columns="bus", values=value_name)
    else:
        profile = raw.drop(columns=[timestamp_column]).copy()
        parsed = pd.to_datetime(raw[timestamp_column], errors="coerce")
        if parsed.isna().any():
            raise OverrideValidationError(f"{label} contains invalid timestamps")
        profile.index = parsed

    if len(profile) != HOURS_PER_YEAR:
        raise OverrideValidationError(
            f"{label} must contain exactly {HOURS_PER_YEAR} hourly timestamps; "
            f"found {len(profile)}"
        )
    if profile.index.duplicated().any() or not profile.index.is_monotonic_increasing:
        raise OverrideValidationError(f"{label} timestamps must be unique and increasing")
    expected = pd.date_range(profile.index[0], periods=HOURS_PER_YEAR, freq="h")
    if not profile.index.equals(expected):
        raise OverrideValidationError(f"{label} timestamps must be contiguous hourly values")
    _validate_known(profile.columns, buses, f"{label} bus column(s)")
    profile = profile.apply(pd.to_numeric, errors="coerce")
    if profile.isna().any().any():
        raise OverrideValidationError(f"{label} contains missing or non-numeric values")
    if bounded and ((profile < 0).any().any() or (profile > 1).any().any()):
        raise OverrideValidationError(f"{label} {value_name} values must be within [0, 1]")
    return profile


def validate_remake_data(data: dict) -> None:
    """Validate the assembled data dict before handing it to PyPSA."""
    required = {"buses", "links", "capacities", "technologies", *PROFILE_KEYS}
    missing = sorted(required - set(data))
    if missing:
        raise OverrideValidationError(f"Loaded data is missing keys: {', '.join(missing)}")

    buses = _bus_ids(data)
    _validate_known(data["capacities"]["bus"], buses, "capacity bus(es)")
    capacities = data["capacities"]
    for column in ("p_nom", "e_nom"):
        if column in capacities:
            numeric = pd.to_numeric(capacities[column], errors="coerce")
            invalid_negative = numeric.lt(0)
            if column == "p_nom":
                # The trusted engine encodes pumped-hydro charging power as a
                # negative p_nom; this is a direction convention, not a
                # negative physical capacity.
                pump = capacities["index_carrier"].astype(str).str.endswith("-pump")
                invalid_negative &= ~pump
            if numeric.isna().any() or invalid_negative.any():
                raise OverrideValidationError(
                    f"capacities.{column} must contain non-negative numeric values"
                )

    technologies = data["technologies"]
    if "efficiency" in technologies:
        efficiency = pd.to_numeric(technologies["efficiency"], errors="coerce")
        if efficiency.isna().any() or efficiency.le(0).any() or efficiency.gt(1).any():
            raise OverrideValidationError("technology efficiencies must be within (0, 1]")

    for key in PROFILE_KEYS:
        profile = data[key]
        if len(profile) != HOURS_PER_YEAR:
            raise OverrideValidationError(
                f"{key} must contain exactly {HOURS_PER_YEAR} rows; found {len(profile)}"
            )

    for key in (
        "nuclear_profiles",
        "other_res_pmax",
        "dsr_ts",
        "wind_onshore",
        "wind_offshore",
        "solar_utility",
        "solar_rooftop",
    ):
        profile = data[key]
        numeric = profile.apply(pd.to_numeric, errors="coerce")
        if numeric.isna().any().any():
            raise OverrideValidationError(f"{key} contains missing/non-numeric values")
        # Some source profiles contain floating-point noise just above one.
        if (numeric < -1e-6).any().any() or (numeric > 1 + 1e-6).any().any():
            raise OverrideValidationError(f"{key} values must be within [0, 1]")
        data[key] = numeric.clip(0.0, 1.0)

    carrier_column = (
        "pypsa_carrier" if "pypsa_carrier" in capacities else "index_carrier"
    )
    capacity_mw = pd.to_numeric(capacities["p_nom"], errors="coerce")

    for profile_key, carrier in VRE_CARRIERS.items():
        profile = data[profile_key]
        if profile.shape[1] == 0:
            raise OverrideValidationError(f"{profile_key} contains no bus profiles")

        required_buses = set(
            capacities.loc[
                capacities[carrier_column].astype(str).eq(carrier)
                & capacity_mw.gt(0),
                "bus",
            ].astype(str)
        )
        missing_buses = sorted(required_buses - set(profile.columns.astype(str)))
        if missing_buses:
            raise OverrideValidationError(
                f"{profile_key} is missing positive-capacity bus profile(s): "
                + ", ".join(missing_buses)
            )

        all_zero_buses = sorted(
            bus for bus in required_buses if not profile[bus].gt(0).any()
        )
        if all_zero_buses:
            raise OverrideValidationError(
                f"{profile_key} has all-zero positive-capacity bus profile(s): "
                + ", ".join(all_zero_buses)
            )


def _read_vre_override(
    path: Path | str,
    buses: set[str],
) -> dict[str, pd.DataFrame]:
    raw = _read_table(path, "VRE override")
    _require_columns(raw, ["technology"], "VRE override")
    unknown = set(raw["technology"].astype(str)) - set(VRE_CARRIERS)
    if unknown:
        raise OverrideValidationError(
            f"Unknown VRE technology value(s): {', '.join(sorted(unknown))}"
        )

    result: dict[str, pd.DataFrame] = {}
    for technology, group in raw.groupby("technology", sort=False):
        timestamp = next(
            (
                column
                for column in ("snapshot", "timestamp", "datetime", "time")
                if column in group
            ),
            None,
        )
        if timestamp is None:
            raise OverrideValidationError(
                "VRE override requires snapshot, timestamp, datetime, or time"
            )
        _require_columns(group, ["bus", "p_max_pu"], "VRE override")
        parsed = pd.to_datetime(group[timestamp], errors="coerce")
        if parsed.isna().any():
            raise OverrideValidationError("VRE override contains invalid timestamps")
        prepared = group.assign(_snapshot=parsed)
        if prepared.duplicated(["_snapshot", "bus"]).any():
            raise OverrideValidationError(
                f"VRE override {technology} contains duplicate timestamp,bus rows"
            )
        profile = prepared.pivot(
            index="_snapshot", columns="bus", values="p_max_pu"
        )
        if len(profile) != HOURS_PER_YEAR:
            raise OverrideValidationError(
                f"VRE override {technology} must contain {HOURS_PER_YEAR} timestamps"
            )
        expected = pd.date_range(profile.index[0], periods=HOURS_PER_YEAR, freq="h")
        if not profile.index.equals(expected):
            raise OverrideValidationError(
                f"VRE override {technology} timestamps must be contiguous hourly values"
            )
        _validate_known(profile.columns, buses, "VRE override bus column(s)")
        profile = profile.apply(pd.to_numeric, errors="coerce")
        if (
            profile.isna().any().any()
            or (profile < 0).any().any()
            or (profile > 1).any().any()
        ):
            raise OverrideValidationError(
                "VRE p_max_pu values must be numeric within [0, 1]"
            )
        result[str(technology)] = profile
    return result


def _merge_profile(base: pd.DataFrame, override: pd.DataFrame) -> pd.DataFrame:
    result = base.copy()
    for column in override.columns:
        result[str(column)] = override[column].to_numpy()
    return result


def _positive_capacity_buses(data: dict, carrier: str) -> set[str]:
    capacities = data["capacities"]
    carrier_column = (
        "pypsa_carrier" if "pypsa_carrier" in capacities else "index_carrier"
    )
    capacity_mw = pd.to_numeric(capacities["p_nom"], errors="coerce")
    return set(
        capacities.loc[
            capacities[carrier_column].astype(str).eq(carrier) & capacity_mw.gt(0),
            "bus",
        ].astype(str)
    )


def _load_profile_with_override(
    data_dir: Path | str,
    key: str,
    climate_year: int,
    override: pd.DataFrame | None,
    complete_for: set[str],
) -> pd.DataFrame:
    """Skip the base table when the supplied override is a complete replacement."""
    if override is not None and complete_for and complete_for.issubset(
        set(override.columns.astype(str))
    ):
        return override
    base = load_hourly_input(data_dir, key, climate_year)
    return base if override is None else _merge_profile(base, override)


def read_generator_availability_override(
    path: Path | str,
    capacities: pd.DataFrame,
) -> pd.DataFrame:
    """Read a complete hourly p_max_pu override keyed by generator input row."""
    override = _read_table(path, "generator availability override")
    timestamp_columns = [
        column for column in ("timestamp", "snapshot") if column in override
    ]
    if len(timestamp_columns) != 1:
        raise OverrideValidationError(
            "generator availability override requires exactly one timestamp column: "
            "timestamp or snapshot"
        )
    timestamp_column = timestamp_columns[0]
    required = [timestamp_column, "bus", "index_carrier", "p_max_pu"]
    _require_columns(override, required, "generator availability override")
    override = override[required].copy()
    if override.empty:
        raise OverrideValidationError("generator availability override is empty")
    override = override.rename(columns={timestamp_column: "snapshot"})
    override["snapshot"] = pd.to_datetime(override["snapshot"], errors="coerce")
    if override["snapshot"].isna().any():
        raise OverrideValidationError(
            "generator availability override contains invalid timestamps"
        )
    if override.duplicated(["snapshot", "bus", "index_carrier"]).any():
        raise OverrideValidationError(
            "generator availability override contains duplicate timestamp,bus,index_carrier rows"
        )
    override["p_max_pu"] = pd.to_numeric(
        override["p_max_pu"], errors="coerce"
    )
    if (
        override["p_max_pu"].isna().any()
        or override["p_max_pu"].lt(0).any()
        or override["p_max_pu"].gt(1).any()
    ):
        raise OverrideValidationError(
            "generator availability p_max_pu values must be numeric within [0, 1]"
        )

    capacity_mw = pd.to_numeric(capacities["p_nom"], errors="coerce")
    known = set(
        zip(
            capacities.loc[capacity_mw.gt(0), "bus"].astype(str),
            capacities.loc[capacity_mw.gt(0), "index_carrier"].astype(str),
        )
    )
    supplied = set(
        zip(
            override["bus"].astype(str),
            override["index_carrier"].astype(str),
        )
    )
    unknown = sorted(supplied - known)
    if unknown:
        shown = ", ".join(f"{bus}/{carrier}" for bus, carrier in unknown[:10])
        raise OverrideValidationError(
            f"Unknown positive-capacity generator availability target(s): {shown}"
        )

    for (bus, carrier), group in override.groupby(
        ["bus", "index_carrier"], sort=False
    ):
        timestamps = pd.DatetimeIndex(group["snapshot"].sort_values())
        if len(timestamps) != HOURS_PER_YEAR or not timestamps.equals(MODEL_SNAPSHOTS):
            raise OverrideValidationError(
                f"generator availability {bus}/{carrier} must contain every 2030 hour"
            )
    return override.sort_values(
        ["bus", "index_carrier", "snapshot"]
    ).reset_index(drop=True)


def load_remake_data(
    data_dir: Path | str,
    climate_year: int,
    gas_price: float | None = None,
    coal_price: float | None = None,
    co2_price: float | None = None,
    capacity_override: Path | str | None = None,
    technology_override: Path | str | None = None,
    nuclear_profile_override: Path | str | None = None,
    demand_override: Path | str | None = None,
    vre_override: Path | str | None = None,
) -> dict:
    """Load CSV/Excel base inputs, apply overrides, and validate the result.

    Complete demand, nuclear, or per-technology VRE overrides bypass their
    corresponding base hourly table. Partial overrides retain and update the
    unsupplied base columns.
    """
    data = load_fixed_inputs(data_dir)
    data["technologies"] = apply_fuel_prices(
        data["technologies"], gas_price, co2_price, coal_price
    )
    data["climate_year"] = climate_year
    if "Climate year start" in data["dsr_static"].columns:
        data["dsr_static"] = data["dsr_static"][
            data["dsr_static"]["Climate year start"].le(climate_year)
            & data["dsr_static"]["Climate year end"].ge(climate_year)
        ].copy()

    if capacity_override is not None:
        apply_capacity_override(data, capacity_override)
    if technology_override is not None:
        apply_technology_override(data, technology_override)

    buses = _bus_ids(data)
    demand = None
    if demand_override is not None:
        demand = read_profile_override(
            demand_override,
            "demand override",
            buses,
            value_name="demand_mw",
            bounded=False,
        )
        if demand.lt(0).any().any():
            raise OverrideValidationError(
                "demand override values must be non-negative MW"
            )

    nuclear = None
    if nuclear_profile_override is not None:
        nuclear = read_profile_override(
            nuclear_profile_override,
            "nuclear profile override",
            buses,
        )

    vre_profiles = (
        _read_vre_override(vre_override, buses) if vre_override is not None else {}
    )

    data["electricity_demand"] = _load_profile_with_override(
        data_dir, "electricity_demand", climate_year, demand, buses
    )
    data["nuclear_profiles"] = _load_profile_with_override(
        data_dir,
        "nuclear_profiles",
        climate_year,
        nuclear,
        _positive_capacity_buses(data, "nuclear"),
    )
    for key, carrier in VRE_CARRIERS.items():
        data[key] = _load_profile_with_override(
            data_dir,
            key,
            climate_year,
            vre_profiles.get(key),
            _positive_capacity_buses(data, carrier),
        )

    for key in (
        "other_res_pmax",
        "dsr_ts",
        "hydro_ror",
        "hydro_reservoir",
        "hydro_pondage",
        "hydro_ps_open",
    ):
        data[key] = load_hourly_input(data_dir, key, climate_year)

    validate_remake_data(data)
    return data
