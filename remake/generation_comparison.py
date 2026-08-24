"""Compare solved daily generation with the company production forecast."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .errors import OverrideValidationError
from .input_data import read_table


def read_production_reference(path: Path | str, zone: str) -> pd.DataFrame:
    """Read the normalized daily company production reference for one zone."""
    frame = read_table(path, "production reference")
    required = {"date", "bus", "technology", "production_gwh"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise OverrideValidationError(
            "Production reference is missing column(s): " + ", ".join(missing)
        )
    frame = frame.loc[frame["bus"].astype(str).eq(zone)].copy()
    if frame.empty:
        raise OverrideValidationError(
            f"Production reference contains no rows for zone {zone}"
        )
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    if frame["date"].isna().any():
        raise OverrideValidationError("Production reference contains invalid dates")
    if frame.duplicated(["date", "technology"]).any():
        raise OverrideValidationError(
            "Production reference contains duplicate date,technology rows"
        )
    frame["production_gwh"] = pd.to_numeric(
        frame["production_gwh"], errors="coerce"
    )
    if frame["production_gwh"].isna().any() or frame["production_gwh"].lt(0).any():
        raise OverrideValidationError(
            "Production reference values must be non-negative numeric GWh"
        )
    for technology, group in frame.groupby("technology"):
        dates = pd.DatetimeIndex(group["date"].sort_values())
        expected = pd.date_range(dates[0], dates[-1], freq="D")
        if len(dates) != 365 or not dates.equals(expected):
            raise OverrideValidationError(
                f"Production reference {technology} must contain 365 consecutive days"
            )
    return frame[["date", "technology", "production_gwh"]].sort_values(
        ["technology", "date"]
    )


def _generator_group(zone: str, name: str, carrier: str) -> str:
    index_carrier = name[len(zone) + 1 :] if name.startswith(f"{zone}-") else name
    direct = {
        "onwind": "onwind",
        "offwind": "offwind",
        "solar-pv-utility": "solar",
        "solar-pv-rooftop": "solar",
        "gas-ccgt": "gas-ccgt",
        "gas-ocgt": "gas-ocgt",
        "coal": "coal",
        "lignite": "lignite",
        "oil-light": "oil-light",
        "other-res": "other-res",
        "hydro-ror-turbine": "hydro-ror",
        "nuclear": "nuclear",
    }
    if index_carrier in direct:
        return direct[index_carrier]
    if index_carrier == "gas-conv" or index_carrier.startswith(
        "chp-gas-conventional"
    ):
        return "gas-conventional"
    normalized = f"{index_carrier} {carrier}".lower()
    if "dsr" in normalized or "demand-response" in normalized:
        return "unmapped:dsr"
    if "hydrogen" in normalized or "h2" in normalized:
        return "unmapped:hydrogen"
    if "slack" in normalized:
        return "unmapped:slack"
    return f"unmapped:other:{carrier}"


def _storage_group(carrier: str) -> str:
    if "battery" in carrier:
        return "battery"
    if carrier in {"hydro-phs", "hydro-phs-pure"}:
        return "hydro-ps"
    if carrier == "hydro-reservoir":
        return "hydro-reservoir"
    return f"unmapped:{carrier}"


def model_daily_generation(network, zone: str) -> pd.DataFrame:
    """Aggregate generator output and positive storage discharge to daily GWh."""
    if zone not in network.buses.index:
        raise OverrideValidationError(f"Zone {zone!r} is not present in the network")
    hourly: dict[str, pd.Series] = {}

    generator_names = network.generators.index[
        network.generators["bus"].astype(str).eq(zone)
    ]
    for name in generator_names:
        if name not in network.generators_t.p.columns:
            continue
        carrier = str(network.generators.loc[name, "carrier"])
        group = _generator_group(zone, str(name), carrier)
        values = pd.to_numeric(network.generators_t.p[name], errors="coerce")
        hourly[group] = hourly.get(group, pd.Series(0.0, index=values.index)) + values

    storage_names = network.storage_units.index[
        network.storage_units["bus"].astype(str).eq(zone)
    ]
    for name in storage_names:
        if name not in network.storage_units_t.p.columns:
            continue
        carrier = str(network.storage_units.loc[name, "carrier"])
        group = _storage_group(carrier)
        values = pd.to_numeric(
            network.storage_units_t.p[name], errors="coerce"
        ).clip(lower=0.0)
        hourly[group] = hourly.get(group, pd.Series(0.0, index=values.index)) + values

    if not hourly:
        raise OverrideValidationError(f"Network contains no generation for zone {zone}")
    rows = []
    for technology, values in hourly.items():
        daily = values.resample("D").sum() / 1000.0
        rows.append(
            pd.DataFrame(
                {
                    "date": daily.index.normalize(),
                    "technology": technology,
                    "model_gwh": daily.to_numpy(dtype=float),
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def _metrics(reference: pd.Series, model: pd.Series) -> dict:
    error = model - reference
    correlation = (
        model.corr(reference)
        if model.nunique(dropna=True) > 1 and reference.nunique(dropna=True) > 1
        else None
    )
    return {
        "observations": int(len(error)),
        "reference_twh": float(reference.sum() / 1000.0),
        "model_twh": float(model.sum() / 1000.0),
        "bias_twh": float(error.sum() / 1000.0),
        "mae_gwh": float(error.abs().mean()),
        "rmse_gwh": float(np.sqrt(np.mean(np.square(error)))),
        "correlation": (
            None if correlation is None or pd.isna(correlation) else float(correlation)
        ),
    }


def compare_generation(
    network,
    reference: pd.DataFrame,
    zone: str,
) -> tuple[pd.DataFrame, dict]:
    """Return an aligned daily table and reconciliation metrics."""
    model = model_daily_generation(network, zone)
    comparable_model = model.loc[~model["technology"].str.startswith("unmapped:")]
    aligned = reference.merge(
        comparable_model,
        on=["date", "technology"],
        how="left",
        validate="one_to_one",
    )
    aligned["model_gwh"] = aligned["model_gwh"].fillna(0.0)
    aligned["error_gwh"] = aligned["model_gwh"] - aligned["production_gwh"]

    by_technology = {
        str(technology): _metrics(group["production_gwh"], group["model_gwh"])
        for technology, group in aligned.groupby("technology", sort=True)
    }
    daily_total = aligned.groupby("date")[["production_gwh", "model_gwh"]].sum()
    unmapped = model.loc[model["technology"].str.startswith("unmapped:")]
    unmapped_twh = {
        str(technology).removeprefix("unmapped:"): float(
            group["model_gwh"].sum() / 1000.0
        )
        for technology, group in unmapped.groupby("technology", sort=True)
    }
    report = {
        "zone": zone,
        "overall": _metrics(daily_total["production_gwh"], daily_total["model_gwh"]),
        "by_technology": by_technology,
        "unmapped_model_generation_twh": unmapped_twh,
    }
    return aligned.sort_values(["technology", "date"]), report
