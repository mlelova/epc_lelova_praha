"""Convert the company daily availability forecast into remake inputs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ._helpers import utc_now, write_json
from .company_capacities import DIRECT_MAPPINGS, EXPECTED_SLUGS
from .errors import OverrideValidationError
from .input_data import HOURS_PER_YEAR, MODEL_SNAPSHOTS, load_hourly_input, read_table


SOURCE_ROW_LABELS = ("symbol_name", "effective_date", "tag", "unit", "timezone")
SYMBOL_PATTERN = re.compile(
    r"^genscape/power/supply/(?P<country>[a-z]{2})\."
    r"(?P<kind>cap_avail|cap_inst|pro)\."
    r"(?:(?P<scope>[a-z]+)\.)?"
    r"(?P<slug>[a-z0-9_]+)\.d\.c$"
)

VRE_SPECS = {
    "wind_onshore": ("wnd_on", ("onwind",)),
    "wind_offshore": ("wnd_off", ("offwind",)),
    "solar_utility": ("spv", ("solar-pv-utility", "solar-pv-rooftop")),
    "solar_rooftop": ("spv", ("solar-pv-utility", "solar-pv-rooftop")),
}

VRE_TARGET_CARRIERS = {
    "wind_onshore": "onwind",
    "wind_offshore": "offwind",
    "solar_utility": "solar-pv-utility",
    "solar_rooftop": "solar-pv-rooftop",
}

PRODUCTION_GROUPS = {
    "onwind": ("wnd_on",),
    "offwind": ("wnd_off",),
    "solar": ("spv",),
    "gas-ccgt": ("ccgt",),
    "gas-ocgt": ("gt",),
    "gas-conventional": ("engine", "gas_boiler"),
    "coal": ("coal",),
    "lignite": ("lig",),
    "oil-light": ("oil",),
    "other-res": ("bio", "geo", "waste"),
    "hydro-ror": ("hydro_ror",),
    "hydro-ps": ("hydro_ps",),
    "hydro-reservoir": ("hydro_res",),
    "battery": ("btry",),
    "nuclear": ("nuc",),
}


@dataclass(frozen=True)
class CompanyAvailabilitySource:
    available_gw: pd.DataFrame
    installed_gw: pd.DataFrame
    production_gwh: pd.DataFrame
    effective_date: str
    tag: str
    timezone: str
    ignored_empty_symbols: tuple[str, ...]
    source_path: Path
    source_sha256: str


@dataclass(frozen=True)
class AvailabilityExtractionResult:
    vre_path: Path
    generator_path: Path
    production_path: Path
    audit_path: Path
    vre_override: pd.DataFrame
    generator_override: pd.DataFrame
    production_reference: pd.DataFrame
    audit: dict


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_value(raw: pd.DataFrame, row: int, columns: list[int], label: str) -> str:
    values = {
        str(raw.iloc[row, column]).strip()
        for column in columns
        if str(raw.iloc[row, column]).strip()
    }
    if len(values) != 1:
        raise OverrideValidationError(
            f"Company availability metadata {label!r} must have one consistent value"
        )
    return values.pop()


def read_company_availability_source(
    source_path: Path | str,
    year: int,
) -> CompanyAvailabilitySource:
    """Parse and validate one year from the company daily supply forecast."""
    source_path = Path(source_path).resolve()
    if not source_path.is_file():
        raise OverrideValidationError(
            f"Company availability source does not exist: {source_path}"
        )
    if source_path.suffix.lower() != ".csv":
        raise OverrideValidationError("Company availability source must be a CSV file")

    try:
        raw = pd.read_csv(
            source_path,
            header=None,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    except Exception as exc:
        raise OverrideValidationError(
            f"Could not read company availability source {source_path}: {exc}"
        ) from exc

    if raw.shape[0] < 6 or raw.shape[1] < 2:
        raise OverrideValidationError("Company availability source has no daily data")
    labels = tuple(raw.iloc[:5, 0].astype(str).str.strip())
    if labels != SOURCE_ROW_LABELS:
        raise OverrideValidationError(
            "Company availability metadata rows must be: "
            + ", ".join(SOURCE_ROW_LABELS)
        )

    symbols = raw.iloc[0, 1:].astype(str).str.strip()
    if symbols.eq("").any() or symbols.duplicated().any():
        raise OverrideValidationError(
            "Company availability symbols must be non-empty and unique"
        )

    data_values = raw.iloc[5:, 1:].astype(str).apply(
        lambda column: column.str.strip()
    )
    nonempty_offsets = [
        offset
        for offset in range(data_values.shape[1])
        if data_values.iloc[:, offset].ne("").any()
    ]
    nonempty_columns = [offset + 1 for offset in nonempty_offsets]
    ignored_columns = [
        offset + 1 for offset in range(data_values.shape[1]) if offset not in nonempty_offsets
    ]

    parsed: dict[str, dict[str, int]] = {
        "cap_avail": {},
        "cap_inst": {},
        "pro": {},
    }
    for column in nonempty_columns:
        symbol = str(raw.iloc[0, column]).strip()
        match = SYMBOL_PATTERN.fullmatch(symbol)
        if match is None:
            raise OverrideValidationError(
                f"Unexpected non-empty company availability symbol: {symbol}"
            )
        if match.group("country") != "de" or match.group("scope") != "bl":
            raise OverrideValidationError(
                f"Company availability symbol must use the Germany baseline namespace: {symbol}"
            )
        kind = match.group("kind")
        slug = match.group("slug")
        if slug not in EXPECTED_SLUGS:
            raise OverrideValidationError(
                f"Unsupported company availability technology: {slug}"
            )
        if slug in parsed[kind]:
            raise OverrideValidationError(
                f"Duplicate company availability series for {kind}.{slug}"
            )
        parsed[kind][slug] = column

        expected_unit = "GWh" if kind == "pro" else "GW"
        unit = str(raw.iloc[3, column]).strip()
        if unit != expected_unit:
            raise OverrideValidationError(
                f"Company availability {kind}.{slug} unit must be {expected_unit!r}; "
                f"found {unit!r}"
            )

    for kind, columns in parsed.items():
        missing = sorted(EXPECTED_SLUGS - set(columns))
        extra = sorted(set(columns) - EXPECTED_SLUGS)
        if missing or extra:
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if extra:
                details.append("unexpected: " + ", ".join(extra))
            raise OverrideValidationError(
                f"Company availability {kind} technology set is invalid ("
                + "; ".join(details)
                + ")"
            )

    effective_date = _metadata_value(raw, 1, nonempty_columns, "effective_date")
    tag = _metadata_value(raw, 2, nonempty_columns, "tag")
    timezone = _metadata_value(raw, 4, nonempty_columns, "timezone")
    if pd.isna(pd.to_datetime(effective_date, errors="coerce")):
        raise OverrideValidationError(
            f"Invalid company availability effective_date: {effective_date!r}"
        )
    if timezone != "CET":
        raise OverrideValidationError(
            f"Company availability timezone must be 'CET'; found {timezone!r}"
        )

    date_text = raw.iloc[5:, 0].astype(str).str.strip()
    dates = pd.to_datetime(
        date_text,
        format="%m/%d/%Y %H:%M",
        errors="coerce",
    )
    if dates.isna().any():
        raise OverrideValidationError(
            "Company availability dates must use MM/DD/YYYY HH:MM"
        )
    if dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise OverrideValidationError(
            "Company availability dates must be unique and increasing"
        )
    date_index = pd.DatetimeIndex(dates)
    if not date_index.equals(
        pd.date_range(date_index[0], periods=len(date_index), freq="D")
    ):
        raise OverrideValidationError(
            "Company availability source must contain consecutive daily rows"
        )

    numeric: dict[str, pd.DataFrame] = {}
    for kind, columns in parsed.items():
        frame = pd.DataFrame(
            {
                slug: pd.to_numeric(raw.iloc[5:, column], errors="coerce").to_numpy()
                for slug, column in columns.items()
            },
            index=pd.DatetimeIndex(date_index, name="date"),
        ).sort_index(axis=1)
        if frame.isna().any().any() or not np.isfinite(frame.to_numpy()).all():
            raise OverrideValidationError(
                f"Company availability {kind} values must be complete and finite"
            )
        if frame.lt(0).any().any():
            raise OverrideValidationError(
                f"Company availability {kind} values must be non-negative"
            )
        numeric[kind] = frame

    expected_dates = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    if len(expected_dates) != 365:
        raise OverrideValidationError(
            "The 8,760-hour remake currently supports non-leap forecast years only"
        )
    for kind, frame in numeric.items():
        selected = frame.loc[frame.index.year == year]
        if not selected.index.equals(expected_dates):
            raise OverrideValidationError(
                f"Company availability {kind} must contain every day of {year}"
            )
        numeric[kind] = selected

    for kind, frame in numeric.items():
        if frame["wnd"].abs().gt(1e-9).any():
            raise OverrideValidationError(
                f"Company availability generic wind {kind}.wnd must be zero"
            )

    return CompanyAvailabilitySource(
        available_gw=numeric["cap_avail"],
        installed_gw=numeric["cap_inst"],
        production_gwh=numeric["pro"],
        effective_date=effective_date,
        tag=tag,
        timezone=timezone,
        ignored_empty_symbols=tuple(
            str(raw.iloc[0, column]).strip() for column in ignored_columns
        ),
        source_path=source_path,
        source_sha256=_sha256(source_path),
    )


def _load_static_capacities(
    capacity_override: Path | str,
    bus: str,
) -> tuple[pd.Series, Path]:
    path = Path(capacity_override).resolve()
    frame = read_table(path, "capacity override")
    required = {"bus", "index_carrier"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise OverrideValidationError(
            "Capacity override is missing column(s): " + ", ".join(missing)
        )
    if {"p_nom_mw", "p_nom"}.issubset(frame.columns):
        raise OverrideValidationError(
            "Capacity override must contain only one of p_nom_mw or p_nom"
        )
    value_column = "p_nom_mw" if "p_nom_mw" in frame else "p_nom"
    if value_column not in frame:
        raise OverrideValidationError("Capacity override requires p_nom_mw")
    selected = frame.loc[frame["bus"].astype(str).eq(bus)].copy()
    if selected.empty:
        raise OverrideValidationError(f"Capacity override has no rows for {bus}")
    if selected["index_carrier"].astype(str).duplicated().any():
        raise OverrideValidationError(
            f"Capacity override contains duplicate index_carrier rows for {bus}"
        )
    selected[value_column] = pd.to_numeric(selected[value_column], errors="coerce")
    if selected[value_column].isna().any() or selected[value_column].lt(0).any():
        raise OverrideValidationError(
            "Capacity override p_nom values must be non-negative numeric MW"
        )
    capacities = selected.set_index("index_carrier")[value_column].astype(float)
    return capacities, path


def _require_capacity_rows(capacities_mw: pd.Series, carriers: tuple[str, ...]) -> None:
    missing = sorted(set(carriers) - set(capacities_mw.index.astype(str)))
    if missing:
        raise OverrideValidationError(
            "Capacity override is missing required carrier(s): " + ", ".join(missing)
        )


def _installed_capacity_crosscheck(
    source: CompanyAvailabilitySource,
    capacities_mw: pd.Series,
) -> list[dict]:
    annual = source.installed_gw.mean()
    checks: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    for source_slug, target in DIRECT_MAPPINGS.items():
        checks.append((source_slug, (source_slug,), (target,)))
    conventional_gas = tuple(
        carrier
        for carrier in capacities_mw.index.astype(str)
        if carrier == "gas-conv" or carrier.startswith("chp-gas-conventional")
    )
    checks.extend(
        [
            (
                "other-res",
                ("bio", "geo", "waste"),
                ("other-res",),
            ),
            (
                "solar",
                ("spv",),
                ("solar-pv-utility", "solar-pv-rooftop"),
            ),
            (
                "conventional-gas",
                ("engine", "gas_boiler"),
                conventional_gas,
            ),
            (
                "pumped-hydro",
                ("hydro_ps",),
                ("hydro-phs-turbine", "hydro-phs-pure-turbine"),
            ),
            (
                "hydro-reservoir",
                ("hydro_res",),
                ("hydro-reservoir-turbine",),
            ),
        ]
    )

    results = []
    for label, source_slugs, target_carriers in checks:
        _require_capacity_rows(capacities_mw, target_carriers)
        expected_mw = float(annual[list(source_slugs)].sum() * 1000.0)
        modeled_mw = float(capacities_mw[list(target_carriers)].sum())
        difference_mw = modeled_mw - expected_mw
        tolerance_mw = max(0.01, abs(expected_mw) * 1e-8)
        if abs(difference_mw) > tolerance_mw:
            raise OverrideValidationError(
                f"Installed-capacity cross-check failed for {label}: "
                f"company={expected_mw:.6f} MW, override={modeled_mw:.6f} MW"
            )
        results.append(
            {
                "label": label,
                "source_slugs": list(source_slugs),
                "target_carriers": list(target_carriers),
                "company_annual_mean_mw": expected_mw,
                "override_mw": modeled_mw,
                "difference_mw": difference_mw,
            }
        )
    return results


def _bounded_daily_shape(values: np.ndarray, target_mean: float) -> np.ndarray:
    """Scale one 24-hour shape to an exact bounded daily mean."""
    values = np.asarray(values, dtype=float)
    if values.shape != (24,) or not np.isfinite(values).all():
        raise OverrideValidationError("VRE base daily shape must contain 24 finite hours")
    if (values < 0).any() or (values > 1 + 1e-6).any():
        raise OverrideValidationError("VRE base daily shape must be within [0, 1]")
    if not 0 <= target_mean <= 1:
        raise OverrideValidationError("VRE target daily mean must be within [0, 1]")
    if target_mean == 0:
        return np.zeros(24)

    values = values.clip(0.0, 1.0)
    maximum_mean = float((values > 0).mean())
    if target_mean > maximum_mean + 1e-9:
        raise OverrideValidationError(
            f"VRE target daily mean {target_mean:.6f} cannot be represented by "
            f"the base shape maximum {maximum_mean:.6f}"
        )

    low = 0.0
    high = 1.0
    while np.minimum(1.0, high * values).mean() < target_mean:
        high *= 2.0
        if high > 1e12:
            raise OverrideValidationError("Could not scale VRE base daily shape")
    for _ in range(80):
        middle = (low + high) / 2.0
        if np.minimum(1.0, middle * values).mean() < target_mean:
            low = middle
        else:
            high = middle
    shaped = np.minimum(1.0, high * values)
    if abs(float(shaped.mean()) - target_mean) > 1e-8:
        raise OverrideValidationError("Scaled VRE profile does not preserve daily mean")
    return shaped


def _availability_ratio(
    available_gw: pd.Series,
    static_capacity_mw: float,
    label: str,
    clipping: list[dict],
) -> pd.Series:
    if static_capacity_mw <= 0:
        if available_gw.gt(0).any():
            raise OverrideValidationError(
                f"Cannot map positive {label} availability to zero static capacity"
            )
        return pd.Series(0.0, index=available_gw.index)
    raw = available_gw * 1000.0 / static_capacity_mw
    for date, value in raw.loc[raw.gt(1 + 1e-9)].items():
        clipping.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "label": label,
                "available_gw": float(available_gw.loc[date]),
                "static_capacity_gw": float(static_capacity_mw / 1000.0),
                "raw_p_max_pu": float(value),
                "clipped_available_mw": float(
                    available_gw.loc[date] * 1000.0 - static_capacity_mw
                ),
            }
        )
    return raw.clip(0.0, 1.0)


def _hourly_vre_override(
    source: CompanyAvailabilitySource,
    capacities_mw: pd.Series,
    data_dir: Path | str,
    climate_year: int,
    bus: str,
    clipping: list[dict],
) -> pd.DataFrame:
    rows = []
    for technology, (source_slug, target_carriers) in VRE_SPECS.items():
        _require_capacity_rows(capacities_mw, target_carriers)
        static_capacity_mw = float(capacities_mw[list(target_carriers)].sum())
        target_daily_mean = _availability_ratio(
            source.available_gw[source_slug],
            static_capacity_mw,
            source_slug,
            clipping,
        )
        base = load_hourly_input(data_dir, technology, climate_year)
        if bus not in base.columns:
            raise OverrideValidationError(
                f"Base {technology} profile is missing bus {bus}"
            )
        shaped = np.empty(HOURS_PER_YEAR)
        base_values = base[bus].to_numpy(dtype=float)
        for day, target in enumerate(target_daily_mean.to_numpy(dtype=float)):
            start = day * 24
            shaped[start : start + 24] = _bounded_daily_shape(
                base_values[start : start + 24], float(target)
            )
        rows.append(
            pd.DataFrame(
                {
                    "snapshot": MODEL_SNAPSHOTS,
                    "technology": technology,
                    "bus": bus,
                    "p_max_pu": shaped,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def _hourly_generator_override(
    source: CompanyAvailabilitySource,
    capacities_mw: pd.Series,
    bus: str,
    clipping: list[dict],
) -> pd.DataFrame:
    mappings = _dispatchable_mappings(capacities_mw)
    rows = []
    for label, source_slugs, target_carriers in mappings:
        _require_capacity_rows(capacities_mw, target_carriers)
        static_capacity_mw = float(capacities_mw[list(target_carriers)].sum())
        available = source.available_gw[list(source_slugs)].sum(axis=1)
        daily_ratio = _availability_ratio(
            available,
            static_capacity_mw,
            label,
            clipping,
        )
        hourly = np.repeat(daily_ratio.to_numpy(dtype=float), 24)
        for carrier in target_carriers:
            rows.append(
                pd.DataFrame(
                    {
                        "timestamp": MODEL_SNAPSHOTS,
                        "bus": bus,
                        "index_carrier": carrier,
                        "p_max_pu": hourly,
                    }
                )
            )
    return pd.concat(rows, ignore_index=True)


def _dispatchable_mappings(
    capacities_mw: pd.Series,
) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    conventional_gas = tuple(
        carrier
        for carrier in capacities_mw.index.astype(str)
        if carrier == "gas-conv" or carrier.startswith("chp-gas-conventional")
    )
    return [
        ("ccgt", ("ccgt",), ("gas-ccgt",)),
        ("gt", ("gt",), ("gas-ocgt",)),
        ("coal", ("coal",), ("coal",)),
        ("lignite", ("lig",), ("lignite",)),
        ("oil", ("oil",), ("oil-light",)),
        (
            "conventional-gas",
            ("engine", "gas_boiler"),
            conventional_gas,
        ),
        (
            "other-res",
            ("bio", "geo", "waste"),
            ("other-res",),
        ),
    ]


def _production_reference(
    source: CompanyAvailabilitySource,
    bus: str,
) -> pd.DataFrame:
    rows = []
    for technology, source_slugs in PRODUCTION_GROUPS.items():
        production = source.production_gwh[list(source_slugs)].sum(axis=1)
        rows.append(
            pd.DataFrame(
                {
                    "date": production.index,
                    "bus": bus,
                    "technology": technology,
                    "production_gwh": production.to_numpy(dtype=float),
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def build_company_availability_overrides(
    source: CompanyAvailabilitySource,
    capacity_override: Path | str,
    data_dir: Path | str,
    bus: str,
    year: int,
    climate_year: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Build hourly overrides and a daily production reference."""
    if bus != "DE00":
        raise OverrideValidationError(
            "The Germany company availability source can only map to bus 'DE00'"
        )
    if set(source.available_gw.index.year) != {year}:
        raise OverrideValidationError(
            "Parsed company availability dates do not match the requested year"
        )

    capacities_mw, capacity_path = _load_static_capacities(capacity_override, bus)
    crosscheck = _installed_capacity_crosscheck(source, capacities_mw)
    clipping: list[dict] = []
    vre = _hourly_vre_override(
        source,
        capacities_mw,
        data_dir,
        climate_year,
        bus,
        clipping,
    )
    generators = _hourly_generator_override(
        source,
        capacities_mw,
        bus,
        clipping,
    )
    production = _production_reference(source, bus)

    vre_potential_twh = {}
    for technology, group in vre.groupby("technology", sort=False):
        source_slug, _ = VRE_SPECS[str(technology)]
        carrier = VRE_TARGET_CARRIERS[str(technology)]
        static_capacity_mw = float(capacities_mw[carrier])
        vre_potential_twh[str(technology)] = float(
            group["p_max_pu"].sum() * static_capacity_mw / 1_000_000.0
        )

    for source_slug, technologies in {
        "wnd_on": ("wind_onshore",),
        "wnd_off": ("wind_offshore",),
        "spv": ("solar_utility", "solar_rooftop"),
    }.items():
        expected_twh = float(
            source.available_gw[source_slug].sum() * 24.0 / 1000.0
        )
        modeled_twh = sum(vre_potential_twh[item] for item in technologies)
        if abs(modeled_twh - expected_twh) > 1e-6:
            raise OverrideValidationError(
                f"Generated {source_slug} profile does not reconcile to company availability"
            )

    annual_production_twh = {
        str(technology): float(group["production_gwh"].sum() / 1000.0)
        for technology, group in production.groupby("technology", sort=False)
    }
    availability_above_installed = []
    for slug in source.available_gw.columns:
        excess = source.available_gw[slug] - source.installed_gw[slug]
        for date, value in excess.loc[excess.gt(1e-9)].items():
            availability_above_installed.append(
                {
                    "date": date.strftime("%Y-%m-%d"),
                    "source_slug": str(slug),
                    "available_gw": float(source.available_gw.loc[date, slug]),
                    "installed_gw": float(source.installed_gw.loc[date, slug]),
                    "excess_gw": float(value),
                }
            )
    audit = {
        "created_at": utc_now(),
        "source": {
            "path": str(source.source_path),
            "sha256": source.source_sha256,
            "effective_date": source.effective_date,
            "tag": source.tag,
            "timezone": source.timezone,
            "ignored_empty_symbols": list(source.ignored_empty_symbols),
        },
        "selection": {
            "bus": bus,
            "forecast_year": year,
            "climate_year_for_intraday_shape": climate_year,
            "daily_rows": int(len(source.available_gw)),
            "hourly_rows": HOURS_PER_YEAR,
        },
        "capacity_override": str(capacity_path),
        "installed_capacity_crosscheck": crosscheck,
        "mappings": {
            "vre": {
                key: {
                    "source": value[0],
                    "target_carrier": VRE_TARGET_CARRIERS[key],
                    "capacity_denominator_carriers": list(value[1]),
                }
                for key, value in VRE_SPECS.items()
            },
            "dispatchable": {
                label: {
                    "sources": list(source_slugs),
                    "target_carriers": list(target_carriers),
                }
                for label, source_slugs, target_carriers in _dispatchable_mappings(
                    capacities_mw
                )
            },
            "production": {
                key: list(value) for key, value in PRODUCTION_GROUPS.items()
            },
            "ignored_operational_series": [
                "btry",
                "hydro_ps",
                "hydro_res",
                "hydro_ror",
                "nuc",
                "wnd",
            ],
        },
        "clipping": {
            "event_count": len(clipping),
            "events": clipping,
            "total_clipped_available_mwh": float(
                sum(event["clipped_available_mw"] * 24.0 for event in clipping)
            ),
        },
        "source_consistency": {
            "availability_above_daily_installed_event_count": len(
                availability_above_installed
            ),
            "availability_above_daily_installed_events": (
                availability_above_installed
            ),
        },
        "annual_totals": {
            "installed_capacity_annual_mean_gw": {
                str(slug): float(value)
                for slug, value in source.installed_gw.mean().items()
            },
            "available_energy_twh": {
                str(slug): float(value * 24.0 / 1000.0)
                for slug, value in source.available_gw.sum().items()
            },
            "vre_potential_twh": vre_potential_twh,
            "production_reference_twh": annual_production_twh,
        },
        "warnings": [
            f"{len(clipping)} daily availability target(s) exceeded the static "
            "annual-mean model capacity and were clipped to p_max_pu=1."
        ]
        if clipping
        else [],
    }
    return vre, generators, production, audit


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(
        temporary,
        index=False,
        float_format="%.8f",
        date_format="%Y-%m-%d %H:%M:%S",
    )
    temporary.replace(path)


def extract_company_availability(
    source_path: Path | str,
    capacity_override: Path | str,
    data_dir: Path | str,
    output_dir: Path | str,
    bus: str = "DE00",
    year: int = 2030,
    climate_year: int = 2009,
) -> AvailabilityExtractionResult:
    """Parse, map, validate, and write company availability artifacts."""
    source = read_company_availability_source(source_path, year)
    vre, generators, production, audit = build_company_availability_overrides(
        source,
        capacity_override,
        data_dir,
        bus,
        year,
        climate_year,
    )
    output_dir = Path(output_dir).resolve()
    slug = f"{bus.lower()}_{year}"
    vre_path = output_dir / f"vre_override_{slug}.csv"
    generator_path = output_dir / f"generator_availability_override_{slug}.csv"
    production_path = output_dir / f"production_reference_{slug}.csv"
    audit_path = output_dir / f"availability_override_{slug}.audit.json"
    audit["outputs"] = {
        "vre_override": str(vre_path),
        "generator_availability_override": str(generator_path),
        "production_reference": str(production_path),
        "audit": str(audit_path),
    }
    _write_csv(vre_path, vre)
    _write_csv(generator_path, generators)
    _write_csv(production_path, production)
    write_json(audit_path, audit)
    return AvailabilityExtractionResult(
        vre_path=vre_path,
        generator_path=generator_path,
        production_path=production_path,
        audit_path=audit_path,
        vre_override=vre,
        generator_override=generators,
        production_reference=production,
        audit=audit,
    )
