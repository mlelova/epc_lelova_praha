"""Convert company monthly installed-capacity forecasts to remake overrides."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ._helpers import utc_now, write_json
from .load_network import OverrideValidationError


SOURCE_ROW_LABELS = ("symbol_name", "effective_date", "tag", "unit", "timezone")
EXPECTED_SLUGS = {
    "bio",
    "btry",
    "ccgt",
    "coal",
    "engine",
    "gas_boiler",
    "geo",
    "gt",
    "hydro_ps",
    "hydro_res",
    "hydro_ror",
    "lig",
    "nuc",
    "oil",
    "spv",
    "waste",
    "wnd",
    "wnd_off",
    "wnd_on",
}
SYMBOL_PATTERN = re.compile(
    r"^genscape/power/supply/(?P<country>[a-z]{2})\.cap_inst\.bl\."
    r"(?P<slug>[a-z0-9_]+)\.d\.c$"
)
DIRECT_MAPPINGS = {
    "ccgt": "gas-ccgt",
    "gt": "gas-ocgt",
    "coal": "coal",
    "lig": "lignite",
    "nuc": "nuclear",
    "oil": "oil-light",
    "hydro_ror": "hydro-ror-turbine",
    "wnd_on": "onwind",
    "wnd_off": "offwind",
}
PHS_FAMILIES = ("hydro-phs", "hydro-phs-pure")


@dataclass(frozen=True)
class CompanyCapacitySource:
    monthly_gw: pd.DataFrame
    effective_date: str
    tag: str
    unit: str
    timezone: str
    country: str
    source_path: Path
    source_sha256: str


@dataclass(frozen=True)
class CapacityExtractionResult:
    capacity_path: Path
    battery_path: Path
    audit_path: Path
    capacity_override: pd.DataFrame
    battery_override: pd.DataFrame
    audit: dict


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _single_metadata_value(raw: pd.DataFrame, row: int, label: str) -> str:
    values = {str(value).strip() for value in raw.iloc[row, 1:] if str(value).strip()}
    if len(values) != 1:
        raise OverrideValidationError(
            f"Company capacity metadata {label!r} must have one consistent value"
        )
    return values.pop()


def read_company_capacity_source(
    source_path: Path | str,
    year: int,
) -> CompanyCapacitySource:
    """Parse and validate the company semicolon/decimal-comma capacity export."""
    source_path = Path(source_path).resolve()
    if not source_path.is_file():
        raise OverrideValidationError(
            f"Company capacity source does not exist: {source_path}"
        )
    try:
        raw = pd.read_csv(
            source_path,
            sep=";",
            header=None,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    except Exception as exc:
        raise OverrideValidationError(
            f"Could not read company capacity source {source_path}: {exc}"
        ) from exc

    if raw.shape[0] < 6 or raw.shape[1] < 2:
        raise OverrideValidationError("Company capacity source has no monthly data")
    labels = tuple(raw.iloc[:5, 0].astype(str).str.strip())
    if labels != SOURCE_ROW_LABELS:
        raise OverrideValidationError(
            "Company capacity metadata rows must be: " + ", ".join(SOURCE_ROW_LABELS)
        )

    symbols = raw.iloc[0, 1:].astype(str).str.strip()
    if symbols.eq("").any() or symbols.duplicated().any():
        raise OverrideValidationError("Company capacity symbols must be non-empty and unique")
    parsed_symbols = [SYMBOL_PATTERN.fullmatch(symbol) for symbol in symbols]
    if any(match is None for match in parsed_symbols):
        invalid = [
            symbol for symbol, match in zip(symbols, parsed_symbols) if match is None
        ]
        raise OverrideValidationError(
            f"Unexpected company capacity symbol(s): {', '.join(invalid)}"
        )
    countries = {match.group("country") for match in parsed_symbols if match is not None}
    if countries != {"de"}:
        raise OverrideValidationError(
            "Company capacity source must contain only the Germany ('de') namespace"
        )
    slugs = [match.group("slug") for match in parsed_symbols if match is not None]
    unexpected = sorted(set(slugs) - EXPECTED_SLUGS)
    missing = sorted(EXPECTED_SLUGS - set(slugs))
    if unexpected or missing:
        details = []
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        raise OverrideValidationError(
            "Company capacity technology set does not match the supported schema ("
            + "; ".join(details)
            + ")"
        )

    effective_date = _single_metadata_value(raw, 1, "effective_date")
    tag = _single_metadata_value(raw, 2, "tag")
    unit = _single_metadata_value(raw, 3, "unit")
    timezone = _single_metadata_value(raw, 4, "timezone")
    if unit != "GW":
        raise OverrideValidationError(
            f"Company capacity unit must be 'GW'; found {unit!r}"
        )
    if pd.isna(pd.to_datetime(effective_date, errors="coerce")):
        raise OverrideValidationError(
            f"Invalid company capacity effective_date: {effective_date!r}"
        )

    data = raw.iloc[5:].copy()
    if data.iloc[:, 0].astype(str).str.strip().eq("").any():
        raise OverrideValidationError("Company capacity source contains an empty date")
    dates = pd.to_datetime(
        data.iloc[:, 0].astype(str).str.strip(),
        format="%d/%m/%Y %H:%M",
        errors="coerce",
    )
    if dates.isna().any():
        raise OverrideValidationError(
            "Company capacity dates must use DD/MM/YYYY HH:MM"
        )
    if dates.duplicated().any():
        raise OverrideValidationError("Company capacity dates must be unique")
    if not (
        dates.dt.year.eq(year)
        & dates.dt.day.eq(1)
        & dates.dt.hour.eq(0)
        & dates.dt.minute.eq(0)
    ).all():
        raise OverrideValidationError(
            f"Company capacity rows must be first-of-month midnight values for {year}"
        )
    if sorted(dates.dt.month.tolist()) != list(range(1, 13)):
        raise OverrideValidationError(
            f"Company capacity source must contain exactly one row for every month of {year}"
        )

    values = data.iloc[:, 1:].copy()
    values.columns = slugs
    values = values.apply(
        lambda column: pd.to_numeric(
            column.astype(str).str.strip().str.replace(",", ".", regex=False),
            errors="coerce",
        )
    )
    values.index = pd.DatetimeIndex(dates, name="date")
    if values.isna().any().any() or not np.isfinite(values.to_numpy()).all():
        raise OverrideValidationError(
            "Company capacity values must be complete, finite decimal-comma numbers"
        )
    if values.lt(0).any().any():
        raise OverrideValidationError("Company capacity values must be non-negative")
    if values["wnd"].abs().gt(1e-9).any():
        raise OverrideValidationError(
            "Generic wind capacity 'wnd' must be zero to avoid double counting wnd_on/wnd_off"
        )

    return CompanyCapacitySource(
        monthly_gw=values.sort_index(),
        effective_date=effective_date,
        tag=tag,
        unit=unit,
        timezone=timezone,
        country="de",
        source_path=source_path,
        source_sha256=_sha256(source_path),
    )


def hour_weighted_annual_mean(monthly_gw: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Return annual GW means and per-month hour weights."""
    hours = pd.Series(
        monthly_gw.index.days_in_month * 24,
        index=monthly_gw.index,
        name="hours",
        dtype=float,
    )
    means = monthly_gw.mul(hours, axis=0).sum(axis=0) / hours.sum()
    return means, hours


def _load_base_capacities(path: Path | str, bus: str) -> tuple[pd.DataFrame, Path]:
    path = Path(path).resolve()
    if not path.is_file():
        raise OverrideValidationError(f"Base capacity table does not exist: {path}")
    base = pd.read_csv(path)
    required = {"bus", "index_carrier", "p_nom", "e_nom"}
    missing = sorted(required - set(base.columns))
    if missing:
        raise OverrideValidationError(
            f"Base capacity table is missing column(s): {', '.join(missing)}"
        )
    selected = base[base["bus"].astype(str).eq(bus)].copy()
    if selected.empty:
        raise OverrideValidationError(f"Bus {bus!r} is missing from the base capacity table")
    if selected["index_carrier"].duplicated().any():
        raise OverrideValidationError(f"Base capacity keys for {bus} are not unique")
    for column in ("p_nom", "e_nom"):
        selected[column] = pd.to_numeric(selected[column], errors="coerce")
        if selected[column].isna().any():
            raise OverrideValidationError(
                f"Base capacities for {bus} contain invalid {column} values"
            )
    return selected.set_index("index_carrier"), path


def _require_base_rows(base: pd.DataFrame, carriers: list[str]) -> None:
    missing = sorted(set(carriers) - set(base.index.astype(str)))
    if missing:
        raise OverrideValidationError(
            f"Base capacity table is missing required carrier(s): {', '.join(missing)}"
        )


def _positive_proportions(base: pd.DataFrame, carriers: list[str], label: str) -> pd.Series:
    _require_base_rows(base, carriers)
    values = base.loc[carriers, "p_nom"].clip(lower=0.0)
    if values.sum() <= 0:
        raise OverrideValidationError(
            f"Cannot derive {label} proportions from zero base capacity"
        )
    return values / values.sum()


def build_company_capacity_overrides(
    source: CompanyCapacitySource,
    base_capacities: Path | str,
    bus: str,
    year: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Map an inspected company source to capacity and battery override tables."""
    if set(source.monthly_gw.index.year) != {year}:
        raise OverrideValidationError(
            f"Parsed company capacity dates do not match requested year {year}"
        )
    if bus != "DE00":
        raise OverrideValidationError(
            "The Germany company capacity source can only be mapped to bus 'DE00'"
        )
    base, base_path = _load_base_capacities(base_capacities, bus)
    annual_gw, month_hours = hour_weighted_annual_mean(source.monthly_gw)

    direct_targets = list(DIRECT_MAPPINGS.values())
    fixed_targets = [
        "other-res",
        "solar-pv-utility",
        "solar-pv-rooftop",
        "gas-conv",
        "hydro-reservoir-turbine",
        "hydro-reservoir-reservoir",
        "battery-charge",
        "battery-discharge",
        "battery-store",
    ]
    phs_targets = [
        f"{family}-{component}"
        for family in PHS_FAMILIES
        for component in ("turbine", "pump", "reservoir")
    ]
    _require_base_rows(base, direct_targets + fixed_targets + phs_targets)

    chp_targets = sorted(
        carrier
        for carrier in base.index.astype(str)
        if carrier.startswith("chp-gas-conventional") and base.loc[carrier, "p_nom"] > 0
    )
    if not chp_targets:
        raise OverrideValidationError(
            "Base capacity table has no positive conventional-gas CHP row"
        )

    solar_targets = ["solar-pv-utility", "solar-pv-rooftop"]
    solar_proportions = _positive_proportions(base, solar_targets, "solar")
    gas_targets = ["gas-conv", *chp_targets]
    gas_proportions = _positive_proportions(base, gas_targets, "conventional gas")
    phs_turbines = [f"{family}-turbine" for family in PHS_FAMILIES]
    phs_proportions = _positive_proportions(base, phs_turbines, "pumped hydro")

    rows: list[dict] = []

    def add(carrier: str, p_nom_mw: float = 0.0, e_nom_mwh: float = 0.0) -> None:
        rows.append(
            {
                "bus": bus,
                "index_carrier": carrier,
                "p_nom_mw": float(p_nom_mw),
                "e_nom_mwh": float(e_nom_mwh),
            }
        )

    for source_slug, target in DIRECT_MAPPINGS.items():
        add(target, annual_gw[source_slug] * 1000.0)

    other_res_mw = annual_gw[["bio", "geo", "waste"]].sum() * 1000.0
    add("other-res", other_res_mw)

    solar_mw = annual_gw["spv"] * 1000.0
    for target in solar_targets:
        add(target, solar_mw * solar_proportions[target])

    conventional_gas_mw = annual_gw[["engine", "gas_boiler"]].sum() * 1000.0
    for target in gas_targets:
        add(target, conventional_gas_mw * gas_proportions[target])

    hydro_res_turbine = "hydro-reservoir-turbine"
    hydro_res_store = "hydro-reservoir-reservoir"
    if (
        base.loc[hydro_res_turbine, "p_nom"] <= 0
        or base.loc[hydro_res_store, "e_nom"] <= 0
    ):
        raise OverrideValidationError(
            "Base hydro-reservoir turbine power and reservoir energy must be positive"
        )
    hydro_res_target_mw = annual_gw["hydro_res"] * 1000.0
    hydro_res_scale = hydro_res_target_mw / base.loc[hydro_res_turbine, "p_nom"]
    add(hydro_res_turbine, hydro_res_target_mw)
    add(
        hydro_res_store,
        e_nom_mwh=base.loc[hydro_res_store, "e_nom"] * hydro_res_scale,
    )

    hydro_ps_mw = annual_gw["hydro_ps"] * 1000.0
    phs_family_scales: dict[str, float] = {}
    for family in PHS_FAMILIES:
        turbine = f"{family}-turbine"
        pump = f"{family}-pump"
        reservoir = f"{family}-reservoir"
        if (
            base.loc[turbine, "p_nom"] <= 0
            or base.loc[pump, "p_nom"] >= 0
            or base.loc[reservoir, "e_nom"] <= 0
        ):
            raise OverrideValidationError(
                f"Base {family} must have positive turbine/energy and negative pump power"
            )
        target_turbine_mw = hydro_ps_mw * phs_proportions[turbine]
        scale = target_turbine_mw / base.loc[turbine, "p_nom"]
        phs_family_scales[family] = float(scale)
        add(turbine, target_turbine_mw)
        add(pump, abs(base.loc[pump, "p_nom"]) * scale)
        add(reservoir, e_nom_mwh=base.loc[reservoir, "e_nom"] * scale)

    if (
        base.loc["battery-discharge", "p_nom"] <= 0
        or base.loc["battery-store", "e_nom"] <= 0
    ):
        raise OverrideValidationError(
            "Base battery discharge power and stored energy must be positive"
        )
    battery_duration_h = (
        base.loc["battery-store", "e_nom"]
        / base.loc["battery-discharge", "p_nom"]
    )
    if not np.isfinite(battery_duration_h) or battery_duration_h <= 0:
        raise OverrideValidationError(
            "Cannot derive a positive battery duration from the base capacity table"
        )
    battery = pd.DataFrame(
        [
            {
                "bus": bus,
                "p_nom_mw": float(annual_gw["btry"] * 1000.0),
                "duration_h": float(battery_duration_h),
            }
        ]
    )

    capacity = pd.DataFrame(rows)
    if capacity.duplicated(["bus", "index_carrier"]).any():
        raise OverrideValidationError("Generated capacity override contains duplicate keys")
    capacity = capacity.sort_values("index_carrier", kind="stable").reset_index(drop=True)

    capacity_audit = []
    for row in capacity.to_dict(orient="records"):
        carrier = row["index_carrier"]
        base_p_nom = abs(float(base.loc[carrier, "p_nom"]))
        base_e_nom = float(base.loc[carrier, "e_nom"])
        capacity_audit.append(
            {
                **row,
                "base_physical_p_nom_mw": base_p_nom,
                "base_e_nom_mwh": base_e_nom,
                "delta_p_nom_mw": float(row["p_nom_mw"] - base_p_nom),
                "delta_e_nom_mwh": float(row["e_nom_mwh"] - base_e_nom),
            }
        )

    audit = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source": {
            "path": str(source.source_path),
            "sha256": source.source_sha256,
            "effective_date": source.effective_date,
            "tag": source.tag,
            "unit": source.unit,
            "timezone": source.timezone,
            "country": source.country,
        },
        "target": {
            "bus": bus,
            "year": year,
            "base_capacity_path": str(base_path),
            "base_capacity_sha256": _sha256(base_path),
        },
        "aggregation": {
            "policy": "hour_weighted_annual_mean",
            "assumption": "Each first-of-month value applies throughout that month",
            "modeled_hours": int(month_hours.sum()),
            "month_hours": {
                timestamp.strftime("%Y-%m"): int(hours)
                for timestamp, hours in month_hours.items()
            },
            "annual_mean_gw": {
                slug: float(value) for slug, value in annual_gw.items()
            },
        },
        "baseline_proportions": {
            "solar": {
                key: float(value) for key, value in solar_proportions.items()
            },
            "conventional_gas": {
                key: float(value) for key, value in gas_proportions.items()
            },
            "pumped_hydro_turbines": {
                key: float(value) for key, value in phs_proportions.items()
            },
            "pumped_hydro_family_scale": phs_family_scales,
            "hydro_reservoir_scale": float(hydro_res_scale),
            "battery_duration_h": float(battery_duration_h),
        },
        "mapping": {
            "direct": DIRECT_MAPPINGS,
            "other_res": ["bio", "geo", "waste"],
            "solar": {"source": "spv", "targets": solar_targets},
            "conventional_gas": {
                "sources": ["engine", "gas_boiler"],
                "targets": gas_targets,
            },
            "hydro_reservoir": {
                "source": "hydro_res",
                "targets": [hydro_res_turbine, hydro_res_store],
            },
            "pumped_hydro": {
                "source": "hydro_ps",
                "families": list(PHS_FAMILIES),
            },
            "battery": {"source": "btry", "output": "battery_override"},
            "generic_wind": {"source": "wnd", "required_value_gw": 0.0},
        },
        "capacity_override": capacity_audit,
        "battery_override": {
            **battery.iloc[0].to_dict(),
            "base_p_nom_mw": float(base.loc["battery-discharge", "p_nom"]),
            "base_duration_h": float(battery_duration_h),
            "delta_p_nom_mw": float(
                battery.loc[0, "p_nom_mw"]
                - base.loc["battery-discharge", "p_nom"]
            ),
        },
        "ignored_zero_sources": ["wnd"],
        "warnings": [],
    }
    return capacity, battery, audit


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, float_format="%.6f")
    temporary.replace(path)


def extract_company_capacities(
    source_path: Path | str,
    base_capacities: Path | str,
    output_dir: Path | str,
    bus: str = "DE00",
    year: int = 2030,
) -> CapacityExtractionResult:
    """Parse, map, and write company capacity override artifacts."""
    source = read_company_capacity_source(source_path, year)
    capacity, battery, audit = build_company_capacity_overrides(
        source,
        base_capacities,
        bus,
        year,
    )
    output_dir = Path(output_dir).resolve()
    slug = f"{bus.lower()}_{year}"
    capacity_path = output_dir / f"capacity_override_{slug}.csv"
    battery_path = output_dir / f"battery_override_{slug}.csv"
    audit_path = output_dir / f"capacity_override_{slug}.audit.json"
    audit["outputs"] = {
        "capacity_override": str(capacity_path),
        "battery_override": str(battery_path),
        "audit": str(audit_path),
    }
    _write_csv(capacity_path, capacity)
    _write_csv(battery_path, battery)
    write_json(audit_path, audit)
    return CapacityExtractionResult(
        capacity_path=capacity_path,
        battery_path=battery_path,
        audit_path=audit_path,
        capacity_override=capacity,
        battery_override=battery,
        audit=audit,
    )
