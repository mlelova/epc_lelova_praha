"""Create a standalone HTML dashboard from a solved PyPSA network."""

from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pypsa


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output"
TEMPLATE_PATH = Path(__file__).resolve().parent / "dashboard_template.html"
EUROPE_SCOPE = "EUROPE"
VRE_PATTERN = re.compile(r"wind|solar", re.IGNORECASE)
RENEWABLE_PATTERN = re.compile(r"wind|solar|hydro|other-res|renewable", re.IGNORECASE)
FALLBACK_COLORS = (
    "#5ac8fa",
    "#ffb020",
    "#9ad36a",
    "#e0567a",
    "#c08af7",
    "#4bb3a8",
    "#6fa3d0",
    "#d98f70",
    "#a8b3c7",
    "#d4c65a",
)


class DashboardError(ValueError):
    """Raised when a network cannot be converted into a dashboard."""


def _series_or_default(
    frame: pd.DataFrame | None,
    index: pd.Index,
    columns: Iterable[Any],
) -> pd.DataFrame:
    columns = list(columns)
    if frame is None or frame.empty:
        return pd.DataFrame(0.0, index=index, columns=columns)
    return frame.reindex(index=index, columns=columns, fill_value=0.0).fillna(0.0)


def _panel_frame(network: pypsa.Network, component: str, attribute: str) -> pd.DataFrame:
    panel = getattr(network, component, None)
    frame = getattr(panel, attribute, None) if panel is not None else None
    if isinstance(frame, pd.DataFrame):
        return frame
    return pd.DataFrame(index=network.snapshots)


def _snapshot_weights(network: pypsa.Network, column: str) -> pd.Series:
    raw = network.snapshot_weightings
    if isinstance(raw, pd.DataFrame):
        if column in raw.columns:
            weights = raw[column]
        elif "objective" in raw.columns:
            weights = raw["objective"]
        else:
            weights = raw.iloc[:, 0]
    else:
        weights = raw
    weights = pd.Series(weights, index=network.snapshots, dtype=float)
    return weights.replace([np.inf, -np.inf], np.nan).fillna(1.0)


def _effective_capacity(table: pd.DataFrame) -> pd.Series:
    nominal = pd.to_numeric(table.get("p_nom", 0.0), errors="coerce").fillna(0.0)
    if "p_nom_opt" not in table:
        return nominal
    optimized = pd.to_numeric(table["p_nom_opt"], errors="coerce")
    return optimized.where(optimized.notna(), nominal).clip(lower=0.0)


def _aggregate_by_bus(
    frame: pd.DataFrame,
    component_table: pd.DataFrame,
    buses: list[Any],
) -> pd.DataFrame:
    if component_table.empty:
        return pd.DataFrame(0.0, index=frame.index, columns=buses)
    aligned = _series_or_default(frame, frame.index, component_table.index)
    mapping = component_table["bus"].reindex(aligned.columns)
    grouped = aligned.T.groupby(mapping, sort=False).sum().T
    return grouped.reindex(columns=buses, fill_value=0.0)


def _finite_number(value: Any, digits: int = 3) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    rounded = round(number, digits)
    return 0.0 if rounded == -0.0 else rounded


def _numbers(values: Iterable[Any], digits: int = 2) -> list[float | None]:
    return [_finite_number(value, digits) for value in values]


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    aligned = pd.concat([values.rename("value"), weights.rename("weight")], axis=1)
    aligned = aligned.replace([np.inf, -np.inf], np.nan).dropna()
    aligned = aligned[aligned.weight > 0]
    if aligned.empty:
        return float("nan")
    return float(np.average(aligned.value, weights=aligned.weight))


def _weighted_std(values: pd.Series, weights: pd.Series) -> float:
    aligned = pd.concat([values.rename("value"), weights.rename("weight")], axis=1)
    aligned = aligned.replace([np.inf, -np.inf], np.nan).dropna()
    aligned = aligned[aligned.weight > 0]
    if aligned.empty:
        return float("nan")
    mean = np.average(aligned.value, weights=aligned.weight)
    return float(np.sqrt(np.average((aligned.value - mean) ** 2, weights=aligned.weight)))


def _weighted_energy(series: pd.Series, weights: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").fillna(0.0)
    return float((values * weights.reindex(values.index).fillna(1.0)).sum())


def _daily_tb_metrics(price: pd.Series) -> dict[str, dict[str, float | None]]:
    clean = pd.to_numeric(price, errors="coerce")
    days = clean.groupby(clean.index.normalize())
    result: dict[str, dict[str, float | None]] = {}
    for n_hours in (1, 2, 4):
        spreads: list[float] = []
        for _, day in days:
            values = day.dropna().to_numpy(dtype=float)
            if len(values) < 2 * n_hours:
                continue
            ordered = np.sort(values)
            spreads.append(float(ordered[-n_hours:].mean() - ordered[:n_hours].mean()))
        spread = float(np.mean(spreads)) if spreads else float("nan")
        result[f"tb{n_hours}"] = {
            "spread_eur_mwh": _finite_number(spread, 2),
            "gross_value_eur_mw_day": _finite_number(spread * n_hours, 2),
        }
    return result


def _monthly_price_metrics(price: pd.Series, weights: pd.Series) -> dict[str, list[Any]]:
    labels: list[str] = []
    averages: list[float | None] = []
    spreads: list[float | None] = []
    negative_hours: list[float | None] = []
    for period, values in price.groupby(price.index.to_period("M")):
        month_weights = weights.reindex(values.index)
        labels.append(str(period))
        averages.append(_finite_number(_weighted_mean(values, month_weights), 2))
        daily = values.groupby(values.index.normalize()).agg(lambda x: x.max() - x.min())
        spreads.append(_finite_number(daily.mean(), 2))
        negative_hours.append(
            _finite_number(month_weights.where(values < 0.0, 0.0).sum(), 1)
        )
    return {
        "labels": labels,
        "average_price": averages,
        "average_daily_spread": spreads,
        "negative_hours": negative_hours,
    }


def _price_payload(price: pd.Series, weights: pd.Series) -> dict[str, Any]:
    price = pd.to_numeric(price, errors="coerce")
    weights = weights.reindex(price.index)
    price_mean = _weighted_mean(price, weights)
    price_std = _weighted_std(price, weights)
    finite_price = price.replace([np.inf, -np.inf], np.nan).dropna()
    daily_spreads = finite_price.groupby(finite_price.index.normalize()).agg(
        lambda values: values.max() - values.min()
    )
    return {
        "hourly": _numbers(price, 2),
        "average_day": _average_day(price, 2),
        "monthly": _monthly_price_metrics(price, weights),
        "tb": _daily_tb_metrics(price),
        "mean": _finite_number(price_mean, 2),
        "std": _finite_number(price_std, 2),
        "cv_pct": _finite_number(
            price_std / abs(price_mean) * 100.0
            if math.isfinite(price_mean) and price_mean != 0
            else float("nan"),
            1,
        ),
        "minimum": _finite_number(
            finite_price.min() if not finite_price.empty else float("nan"), 2
        ),
        "maximum": _finite_number(
            finite_price.max() if not finite_price.empty else float("nan"), 2
        ),
        "p05": _finite_number(
            finite_price.quantile(0.05) if not finite_price.empty else float("nan"),
            2,
        ),
        "p95": _finite_number(
            finite_price.quantile(0.95) if not finite_price.empty else float("nan"),
            2,
        ),
        "negative_hours": _finite_number(weights.where(price < 0.0, 0.0).sum(), 1),
        "average_daily_spread": _finite_number(daily_spreads.mean(), 2),
    }


def _demand_weighted_price(
    prices: pd.DataFrame,
    demand_by_bus: pd.DataFrame,
) -> pd.Series:
    numeric_prices = prices.apply(pd.to_numeric, errors="coerce")
    available_demand = demand_by_bus.where(numeric_prices.notna(), 0.0)
    total_demand = available_demand.sum(axis=1)
    weighted_sum = (numeric_prices * available_demand).sum(axis=1, min_count=1)
    weighted_price = weighted_sum.div(total_demand.where(total_demand.ne(0.0)))
    return weighted_price.fillna(numeric_prices.mean(axis=1, skipna=True))


def _average_day(series: pd.Series, digits: int = 2) -> list[float | None]:
    grouped = series.groupby(series.index.hour).mean().reindex(range(24))
    return _numbers(grouped, digits)


def _regression_payload(
    residual_load_mw: pd.Series,
    price: pd.Series,
    max_points: int = 1200,
) -> dict[str, Any] | None:
    frame = pd.concat(
        [residual_load_mw.rename("residual"), price.rename("price")], axis=1
    ).replace([np.inf, -np.inf], np.nan).dropna()
    if len(frame) < 2 or frame.residual.nunique() < 2 or frame.price.nunique() < 2:
        return None
    x = frame.residual.to_numpy(dtype=float) / 1000.0
    y = frame.price.to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    correlation = np.corrcoef(x, y)[0, 1]
    if len(frame) > max_points:
        positions = np.linspace(0, len(frame) - 1, max_points, dtype=int)
        x = x[positions]
        y = y[positions]
    return {
        "points": [
            [_finite_number(x_value, 3), _finite_number(y_value, 2)]
            for x_value, y_value in zip(x, y)
        ],
        "slope_eur_mwh_per_gw": _finite_number(slope, 3),
        "intercept_eur_mwh": _finite_number(intercept, 2),
        "correlation": _finite_number(correlation, 3),
    }


def _carrier_metadata(network: pypsa.Network, carriers: Iterable[str]) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    carrier_table = network.carriers
    for position, carrier in enumerate(sorted(set(map(str, carriers)))):
        label = carrier.replace("-", " ").replace("_", " ").title()
        color = FALLBACK_COLORS[position % len(FALLBACK_COLORS)]
        if carrier in carrier_table.index:
            nice_name = carrier_table.at[carrier, "nice_name"] if "nice_name" in carrier_table else ""
            raw_color = carrier_table.at[carrier, "color"] if "color" in carrier_table else ""
            if isinstance(nice_name, str) and nice_name.strip():
                label = nice_name.strip()
            if isinstance(raw_color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", raw_color.strip()):
                color = raw_color.strip()
        metadata[carrier] = {"label": label, "color": color}
    return metadata


def _generation_scope_payload(
    annual_rows: pd.DataFrame,
    hourly_rows: pd.DataFrame,
    carrier_meta: dict[str, dict[str, str]],
    total_weight: float,
) -> dict[str, Any]:
    carrier_rows: list[dict[str, Any]] = []
    for carrier, row in annual_rows.iterrows():
        carrier_name = str(carrier)
        energy = float(row.energy_mwh)
        capacity = float(row.capacity_mw)
        capacity_factor = (
            energy / (capacity * total_weight) * 100.0
            if capacity > 0 and total_weight > 0
            else float("nan")
        )
        carrier_rows.append(
            {
                "carrier": carrier_name,
                **carrier_meta[carrier_name],
                "energy_twh": _finite_number(energy / 1e6, 6),
                "capacity_gw": _finite_number(capacity / 1000.0, 3),
                "capacity_factor_pct": _finite_number(capacity_factor, 1),
            }
        )
    carrier_rows.sort(key=lambda row: abs(row["energy_twh"] or 0.0), reverse=True)

    profiles: list[dict[str, Any]] = []
    ranked = [row["carrier"] for row in carrier_rows[:8]]
    for carrier in ranked:
        if carrier not in hourly_rows.index:
            continue
        profiles.append(
            {
                "carrier": carrier,
                **carrier_meta[carrier],
                "values_gw": _numbers(hourly_rows.loc[carrier] / 1000.0, 3),
            }
        )
    other = hourly_rows.drop(index=ranked, errors="ignore").sum(axis=0)
    if np.abs(other.to_numpy(dtype=float)).max(initial=0.0) > 1e-9:
        profiles.append(
            {
                "carrier": "other",
                "label": "Other",
                "color": "#7d8aa0",
                "values_gw": _numbers(other / 1000.0, 3),
            }
        )

    total_generation = float(annual_rows.energy_mwh.sum()) if not annual_rows.empty else 0.0
    renewable_generation = (
        float(
            sum(
                row.energy_mwh
                for carrier, row in annual_rows.iterrows()
                if RENEWABLE_PATTERN.search(str(carrier))
            )
        )
        if not annual_rows.empty
        else 0.0
    )
    return {
        "carriers": carrier_rows,
        "average_day": profiles,
        "total_generation_twh": _finite_number(total_generation / 1e6, 6),
        "renewable_share_pct": _finite_number(
            renewable_generation / total_generation * 100.0
            if total_generation > 0
            else float("nan"),
            1,
        ),
    }


def _generation_payload(
    network: pypsa.Network,
    buses: list[Any],
    generator_dispatch: pd.DataFrame,
    generator_weights: pd.Series,
) -> tuple[
    dict[str, dict[str, Any]],
    pd.DataFrame,
    pd.DataFrame,
]:
    generators = network.generators.copy()
    dispatch = _series_or_default(
        generator_dispatch, network.snapshots, generators.index
    )
    generators["_capacity"] = _effective_capacity(generators)
    generators["_carrier"] = generators.get("carrier", "other").fillna("other").astype(str)
    carrier_meta = _carrier_metadata(network, generators["_carrier"])

    annual_by_generator = dispatch.mul(generator_weights, axis=0).sum(axis=0)
    annual_table = pd.DataFrame(
        {
            "bus": generators["bus"],
            "carrier": generators["_carrier"],
            "energy_mwh": annual_by_generator.reindex(generators.index).fillna(0.0),
            "capacity_mw": generators["_capacity"],
        }
    )
    annual_grouped = annual_table.groupby(["bus", "carrier"], sort=False).sum()

    hourly = dispatch.groupby(dispatch.index.hour).mean().reindex(range(24), fill_value=0.0)
    hourly_by_group = hourly.T.assign(
        bus=generators["bus"], carrier=generators["_carrier"]
    ).groupby(["bus", "carrier"], sort=False).sum(numeric_only=True)

    result: dict[str, dict[str, Any]] = {}
    total_weight = float(generator_weights.sum())
    for bus in buses:
        if bus in annual_grouped.index.get_level_values(0):
            rows = annual_grouped.xs(bus, level=0).copy()
        else:
            rows = pd.DataFrame(columns=["energy_mwh", "capacity_mw"])
        if bus in hourly_by_group.index.get_level_values(0):
            bus_profiles = hourly_by_group.xs(bus, level=0)
        else:
            bus_profiles = pd.DataFrame(columns=range(24), dtype=float)
        result[str(bus)] = _generation_scope_payload(
            rows, bus_profiles, carrier_meta, total_weight
        )

    europe_annual = annual_table.groupby("carrier", sort=False)[
        ["energy_mwh", "capacity_mw"]
    ].sum()
    europe_hourly = hourly.T.assign(carrier=generators["_carrier"]).groupby(
        "carrier", sort=False
    ).sum(numeric_only=True)
    result[EUROPE_SCOPE] = _generation_scope_payload(
        europe_annual, europe_hourly, carrier_meta, total_weight
    )

    vre_generators = generators.index[
        generators["_carrier"].map(lambda value: bool(VRE_PATTERN.search(value)))
    ]
    vre_by_bus = _aggregate_by_bus(dispatch.reindex(columns=vre_generators), generators.loc[vre_generators], buses)
    all_generation_by_bus = _aggregate_by_bus(dispatch, generators, buses)
    return result, vre_by_bus, all_generation_by_bus


def _battery_payloads(
    network: pypsa.Network,
    buses: list[Any],
    prices: pd.DataFrame,
    storage_weights: pd.Series,
    objective_weights: pd.Series,
) -> dict[str, dict[str, Any] | None]:
    storage = network.storage_units.copy()
    if storage.empty:
        return {**{str(bus): None for bus in buses}, EUROPE_SCOPE: None}
    carriers = storage.get("carrier", "").fillna("").astype(str)
    battery_units = storage.index[carriers.str.contains("battery", case=False, na=False)]
    if battery_units.empty:
        return {**{str(bus): None for bus in buses}, EUROPE_SCOPE: None}

    battery = storage.loc[battery_units].copy()
    battery["_capacity"] = _effective_capacity(battery)
    battery["_energy"] = battery["_capacity"] * pd.to_numeric(
        battery.get("max_hours", 0.0), errors="coerce"
    ).fillna(0.0)
    dispatch = _series_or_default(
        _panel_frame(network, "storage_units_t", "p"), network.snapshots, battery_units
    )
    state_of_charge = _series_or_default(
        _panel_frame(network, "storage_units_t", "state_of_charge"),
        network.snapshots,
        battery_units,
    )

    def build_payload(
        units: pd.Index,
        *,
        use_local_unit_prices: bool,
    ) -> dict[str, Any]:
        capacity_mw = float(battery.loc[units, "_capacity"].sum())
        energy_mwh = float(battery.loc[units, "_energy"].sum())
        unit_dispatch = dispatch[units]
        zone_dispatch = unit_dispatch.sum(axis=1)
        zone_soc = state_of_charge[units].sum(axis=1)
        if use_local_unit_prices:
            unit_charge = -unit_dispatch.clip(upper=0.0)
            unit_discharge = unit_dispatch.clip(lower=0.0)
            charge = unit_charge.sum(axis=1).astype(float)
            discharge = unit_discharge.sum(axis=1).astype(float)
            local_prices = pd.concat(
                [
                    pd.to_numeric(prices[battery.at[unit, "bus"]], errors="coerce")
                    for unit in units
                ],
                axis=1,
            )
            local_prices.columns = units
            revenue_by_hour = (unit_dispatch * local_prices).sum(axis=1, min_count=1)
            charge_cost_by_hour = (unit_charge * local_prices).sum(axis=1, min_count=1)
            discharge_value_by_hour = (
                unit_discharge * local_prices
            ).sum(axis=1, min_count=1)
        else:
            charge = (-zone_dispatch.clip(upper=0.0)).astype(float)
            discharge = zone_dispatch.clip(lower=0.0).astype(float)
            price = pd.to_numeric(
                prices[battery.at[units[0], "bus"]], errors="coerce"
            )
            revenue_by_hour = zone_dispatch * price
            charge_cost_by_hour = charge * price
            discharge_value_by_hour = discharge * price
        charge_mwh = _weighted_energy(charge, storage_weights)
        discharge_mwh = _weighted_energy(discharge, storage_weights)
        gross_revenue = _weighted_energy(revenue_by_hour, objective_weights)
        objective_charge_mwh = _weighted_energy(charge, objective_weights)
        objective_discharge_mwh = _weighted_energy(discharge, objective_weights)
        weighted_buy = (
            _weighted_energy(charge_cost_by_hour, objective_weights)
            / objective_charge_mwh
            if objective_charge_mwh > 0
            else float("nan")
        )
        weighted_sell = (
            _weighted_energy(discharge_value_by_hour, objective_weights)
            / objective_discharge_mwh
            if objective_discharge_mwh > 0
            else float("nan")
        )
        cap_weights = battery.loc[units, "_capacity"]

        def capacity_weighted(column: str) -> float:
            if column not in battery or cap_weights.sum() <= 0:
                return float("nan")
            values = pd.to_numeric(battery.loc[units, column], errors="coerce")
            valid = values.notna() & cap_weights.notna()
            return (
                float(np.average(values[valid], weights=cap_weights[valid]))
                if valid.any() and cap_weights[valid].sum() > 0
                else float("nan")
            )

        monthly_labels: list[str] = []
        monthly_revenue: list[float | None] = []
        for period, values in revenue_by_hour.groupby(
            revenue_by_hour.index.to_period("M")
        ):
            monthly_labels.append(str(period))
            monthly_revenue.append(
                _finite_number(
                    _weighted_energy(
                        values,
                        objective_weights.reindex(values.index),
                    )
                    / 1e6,
                    3,
                )
            )

        soc_pct = zone_soc / energy_mwh * 100.0 if energy_mwh > 0 else zone_soc * np.nan
        return {
            "power_gw": _finite_number(capacity_mw / 1000.0, 4),
            "energy_gwh": _finite_number(energy_mwh / 1000.0, 4),
            "duration_h": _finite_number(energy_mwh / capacity_mw if capacity_mw > 0 else float("nan"), 2),
            "efficiency_store_pct": _finite_number(capacity_weighted("efficiency_store") * 100.0, 1),
            "efficiency_dispatch_pct": _finite_number(capacity_weighted("efficiency_dispatch") * 100.0, 1),
            "round_trip_efficiency_pct": _finite_number(
                capacity_weighted("efficiency_store")
                * capacity_weighted("efficiency_dispatch")
                * 100.0,
                1,
            ),
            "charge_twh": _finite_number(charge_mwh / 1e6, 6),
            "discharge_twh": _finite_number(discharge_mwh / 1e6, 6),
            "realized_efficiency_pct": _finite_number(
                discharge_mwh / charge_mwh * 100.0 if charge_mwh > 0 else float("nan"),
                1,
            ),
            "equivalent_cycles": _finite_number(
                discharge_mwh / energy_mwh if energy_mwh > 0 else float("nan"), 1
            ),
            "utilization_pct": _finite_number(
                discharge_mwh / (capacity_mw * storage_weights.sum()) * 100.0
                if capacity_mw > 0 and storage_weights.sum() > 0
                else float("nan"),
                1,
            ),
            "weighted_charge_price": _finite_number(weighted_buy, 2),
            "weighted_discharge_price": _finite_number(weighted_sell, 2),
            "realized_spread": _finite_number(weighted_sell - weighted_buy, 2),
            "gross_revenue_meur": _finite_number(gross_revenue / 1e6, 3),
            "gross_revenue_keur_mw": _finite_number(
                gross_revenue / capacity_mw / 1000.0 if capacity_mw > 0 else float("nan"),
                2,
            ),
            "dispatch_gw": _numbers(zone_dispatch / 1000.0, 3),
            "soc_pct": _numbers(soc_pct, 2),
            "average_day": {
                "charge_gw": _average_day(charge / 1000.0, 3),
                "discharge_gw": _average_day(discharge / 1000.0, 3),
                "soc_pct": _average_day(soc_pct, 2),
            },
            "monthly_revenue": {
                "labels": monthly_labels,
                "values_meur": monthly_revenue,
            },
        }

    result: dict[str, dict[str, Any] | None] = {}
    for bus in buses:
        units = battery.index[battery["bus"] == bus]
        result[str(bus)] = (
            build_payload(units, use_local_unit_prices=False)
            if not units.empty
            else None
        )
    result[EUROPE_SCOPE] = build_payload(
        battery.index, use_local_unit_prices=True
    )
    return result


def _flow_payloads(
    network: pypsa.Network,
    buses: list[Any],
    weights: pd.Series,
) -> dict[str, dict[str, Any]]:
    links = network.links
    p0 = _panel_frame(network, "links_t", "p0")
    p1 = _panel_frame(network, "links_t", "p1")
    if links.empty or p0.empty or p1.empty:
        empty_zones = {
            str(bus): {
                "mode": "zonal",
                "net_import_twh": 0.0,
                "imports_twh": 0.0,
                "exports_twh": 0.0,
                "neighbors": [],
            }
            for bus in buses
        }
        empty_zones[EUROPE_SCOPE] = {
            "mode": "internal",
            "internal_transfer_twh": 0.0,
            "transmission_losses_twh": 0.0,
            "active_corridors": 0,
            "corridors": [],
        }
        return empty_zones
    p0 = _series_or_default(p0, network.snapshots, links.index)
    p1 = _series_or_default(p1, network.snapshots, links.index)
    result: dict[str, dict[str, Any]] = {}
    for bus in buses:
        totals: dict[str, dict[str, float]] = {}
        zone_net = pd.Series(0.0, index=network.snapshots)
        incident = links.index[(links.bus0 == bus) | (links.bus1 == bus)]
        for link in incident:
            if links.at[link, "bus0"] == bus:
                other = str(links.at[link, "bus1"])
                injection = -p0[link]
            else:
                other = str(links.at[link, "bus0"])
                injection = -p1[link]
            zone_net = zone_net.add(injection, fill_value=0.0)
            entry = totals.setdefault(other, {"imports": 0.0, "exports": 0.0, "net": 0.0})
            entry["imports"] += _weighted_energy(injection.clip(lower=0.0), weights)
            entry["exports"] += _weighted_energy((-injection.clip(upper=0.0)), weights)
            entry["net"] += _weighted_energy(injection, weights)
        neighbors = [
            {
                "zone": other,
                "imports_twh": _finite_number(values["imports"] / 1e6, 6),
                "exports_twh": _finite_number(values["exports"] / 1e6, 6),
                "net_import_twh": _finite_number(values["net"] / 1e6, 6),
            }
            for other, values in totals.items()
        ]
        neighbors.sort(
            key=lambda row: (row["imports_twh"] or 0.0) + (row["exports_twh"] or 0.0),
            reverse=True,
        )
        result[str(bus)] = {
            "mode": "zonal",
            "net_import_twh": _finite_number(_weighted_energy(zone_net, weights) / 1e6, 6),
            "imports_twh": _finite_number(
                sum((row["imports_twh"] or 0.0) for row in neighbors), 6
            ),
            "exports_twh": _finite_number(
                sum((row["exports_twh"] or 0.0) for row in neighbors), 6
            ),
            "neighbors": neighbors,
        }

    bus_set = set(buses)
    internal_links = links.index[
        links.bus0.isin(bus_set)
        & links.bus1.isin(bus_set)
        & links.bus0.ne(links.bus1)
    ]
    corridor_totals: dict[tuple[str, str], dict[str, float]] = {}
    total_transfer_mwh = 0.0
    total_losses_mwh = 0.0
    for link in internal_links:
        sent = p0[link].clip(lower=0.0) + p1[link].clip(lower=0.0)
        delivered = -p0[link].clip(upper=0.0) - p1[link].clip(upper=0.0)
        transfer_mwh = _weighted_energy(sent, weights)
        losses_mwh = _weighted_energy(sent - delivered, weights)
        total_transfer_mwh += transfer_mwh
        total_losses_mwh += losses_mwh
        endpoints = tuple(
            sorted((str(links.at[link, "bus0"]), str(links.at[link, "bus1"])))
        )
        entry = corridor_totals.setdefault(
            endpoints, {"throughput_mwh": 0.0, "losses_mwh": 0.0}
        )
        entry["throughput_mwh"] += transfer_mwh
        entry["losses_mwh"] += losses_mwh
    corridors = [
        {
            "corridor": f"{endpoints[0]} ↔ {endpoints[1]}",
            "throughput_twh": _finite_number(values["throughput_mwh"] / 1e6, 6),
            "losses_twh": _finite_number(values["losses_mwh"] / 1e6, 6),
        }
        for endpoints, values in corridor_totals.items()
        if values["throughput_mwh"] > 1e-9
    ]
    corridors.sort(key=lambda row: row["throughput_twh"] or 0.0, reverse=True)
    result[EUROPE_SCOPE] = {
        "mode": "internal",
        "internal_transfer_twh": _finite_number(total_transfer_mwh / 1e6, 6),
        "transmission_losses_twh": _finite_number(total_losses_mwh / 1e6, 6),
        "active_corridors": len(corridors),
        "corridors": corridors,
    }
    return result


def _build_dashboard_data(
    network: pypsa.Network,
    source_path: Path,
    default_zone: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    snapshots = network.snapshots
    if not isinstance(snapshots, pd.DatetimeIndex) or snapshots.empty:
        raise DashboardError("The network must use a non-empty DatetimeIndex for snapshots.")
    if snapshots.has_duplicates or not snapshots.is_monotonic_increasing:
        raise DashboardError("Network snapshots must be unique and sorted chronologically.")

    buses = list(network.buses.index)
    if not buses:
        raise DashboardError("The network does not contain any buses.")
    bus_names = [str(bus) for bus in buses]
    if len(set(bus_names)) != len(bus_names):
        raise DashboardError("Bus names must be unique when converted to text.")
    if EUROPE_SCOPE in bus_names:
        raise DashboardError(
            f"Bus name '{EUROPE_SCOPE}' is reserved for the modeled-Europe aggregate."
        )

    price_frame = _panel_frame(network, "buses_t", "marginal_price")
    if price_frame.empty or not price_frame.notna().any().any():
        raise DashboardError(
            "No bus marginal prices were found. The .nc file does not appear to contain solved results."
        )
    dispatch_frame = _panel_frame(network, "generators_t", "p")
    if dispatch_frame.empty:
        raise DashboardError(
            "No generator dispatch was found. The .nc file does not appear to contain solved results."
        )
    prices = price_frame.reindex(index=snapshots, columns=buses).apply(
        pd.to_numeric, errors="coerce"
    )

    selected_default = default_zone or ("DE00" if "DE00" in bus_names else bus_names[0])
    available_scopes = [EUROPE_SCOPE, *bus_names]
    if selected_default not in available_scopes:
        raise DashboardError(
            f"Unknown default zone '{selected_default}'. Available scopes: "
            f"{', '.join(available_scopes)}"
        )

    objective_weights = _snapshot_weights(network, "objective")
    generator_weights = _snapshot_weights(network, "generators")
    storage_weights = _snapshot_weights(network, "stores")

    loads = network.loads.copy()
    load_frame = _panel_frame(network, "loads_t", "p_set")
    if load_frame.empty:
        load_frame = _panel_frame(network, "loads_t", "p")
    load_by_bus = _aggregate_by_bus(load_frame, loads, buses)

    generation, vre_by_bus, _ = _generation_payload(
        network, buses, dispatch_frame, generator_weights
    )
    residual_load = load_by_bus - vre_by_bus
    batteries = _battery_payloads(
        network, buses, prices, storage_weights, objective_weights
    )
    flows = _flow_payloads(network, buses, generator_weights)

    europe_price = _demand_weighted_price(prices, load_by_bus)
    europe_load = load_by_bus.sum(axis=1)
    europe_residual_load = residual_load.sum(axis=1)
    has_europe_load = not loads.empty and loads.bus.isin(buses).any()
    europe_demand_mwh = _weighted_energy(europe_load, generator_weights)
    zone_payload: dict[str, dict[str, Any]] = {
        EUROPE_SCOPE: {
            "label": f"Europe · all {len(buses)} modeled zones",
            "country": "",
            "is_aggregate": True,
            "member_zones": bus_names,
            "has_load": bool(has_europe_load),
            "price": _price_payload(europe_price, objective_weights),
            "demand": {
                "annual_twh": (
                    _finite_number(europe_demand_mwh / 1e6, 6)
                    if has_europe_load
                    else None
                ),
                "average_day_gw": _average_day(europe_load / 1000.0, 3),
            },
            "residual_load": {
                "average_day_gw": _average_day(
                    europe_residual_load / 1000.0, 3
                ),
                "regression": _regression_payload(
                    europe_residual_load, europe_price
                ),
            },
            "generation": generation[EUROPE_SCOPE],
            "battery": batteries[EUROPE_SCOPE],
            "flows": flows[EUROPE_SCOPE],
        }
    }
    for bus, bus_name in zip(buses, bus_names):
        price = pd.to_numeric(prices[bus], errors="coerce")
        weights = objective_weights.reindex(price.index)
        demand_mwh = _weighted_energy(load_by_bus[bus], generator_weights)
        country = ""
        if "country" in network.buses and bus in network.buses.index:
            raw_country = network.buses.at[bus, "country"]
            if isinstance(raw_country, str):
                country = raw_country
        battery = batteries[bus_name]
        zone_payload[bus_name] = {
            "label": f"{bus_name} · {country}" if country and country != bus_name else bus_name,
            "country": country,
            "is_aggregate": False,
            "member_zones": [bus_name],
            "has_load": bool(loads.bus.eq(bus).any()) if not loads.empty else False,
            "price": _price_payload(price, weights),
            "demand": {
                "annual_twh": _finite_number(demand_mwh / 1e6, 6) if loads.bus.eq(bus).any() else None,
                "average_day_gw": _average_day(load_by_bus[bus] / 1000.0, 3),
            },
            "residual_load": {
                "average_day_gw": _average_day(residual_load[bus] / 1000.0, 3),
                "regression": _regression_payload(residual_load[bus], price),
            },
            "generation": generation[bus_name],
            "battery": battery,
            "flows": flows[bus_name],
        }

    network_name = str(network.name).strip() if getattr(network, "name", None) else source_path.stem
    dashboard_title = title.strip() if title and title.strip() else f"{network_name} — Battery Dashboard"
    return {
        "schema_version": 2,
        "title": dashboard_title,
        "default_zone": selected_default,
        "timestamps": [timestamp.isoformat() for timestamp in snapshots],
        "network": {
            "name": network_name,
            "source_file": source_path.name,
            "start": snapshots[0].isoformat(),
            "end": snapshots[-1].isoformat(),
            "snapshots": len(snapshots),
            "zones": len(buses),
            "objective": _finite_number(getattr(network, "_objective", float("nan")), 2),
        },
        "zones": zone_payload,
    }


def generate_dashboard(
    input_path: str | Path,
    output_path: str | Path | None = None,
    default_zone: str | None = None,
    title: str | None = None,
) -> Path:
    """Generate a standalone HTML dashboard from one solved PyPSA ``.nc`` file.

    Parameters
    ----------
    input_path:
        Path to a solved PyPSA NetCDF network.
    output_path:
        Destination HTML path. Defaults to ``visualisation/output``.
    default_zone:
        Initially selected bus or ``EUROPE`` aggregate. Defaults to ``DE00``
        when available.
    title:
        Optional dashboard title.

    Returns
    -------
    pathlib.Path
        The absolute path to the generated HTML file.
    """
    source = Path(input_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Solved network not found: {source}")
    if not source.is_file():
        raise DashboardError(f"Input path is not a file: {source}")
    if source.suffix.lower() != ".nc":
        raise DashboardError(f"Expected a .nc file, received: {source.name}")

    destination = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else (DEFAULT_OUTPUT_DIR / f"{source.stem}_dashboard.html").resolve()
    )
    if destination.suffix.lower() != ".html":
        raise DashboardError(f"Output path must end with .html: {destination}")

    try:
        network = pypsa.Network()
        network.import_from_netcdf(str(source))
    except Exception as exc:  # PyPSA raises backend-specific exceptions.
        raise DashboardError(f"Could not read PyPSA network '{source}': {exc}") from exc

    data = _build_dashboard_data(network, source, default_zone=default_zone, title=title)
    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise DashboardError(f"Dashboard template could not be read: {TEMPLATE_PATH}") from exc

    payload = json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    payload = payload.replace("<", "\\u003c").replace("&", "\\u0026")
    rendered = template.replace("__DASHBOARD_TITLE__", html.escape(data["title"]))
    rendered = rendered.replace("__DASHBOARD_DATA__", payload)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    temporary.replace(destination)
    return destination


__all__ = ["DashboardError", "generate_dashboard"]
