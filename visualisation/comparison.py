"""Create a standalone comparison dashboard from two solved PyPSA networks."""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any

import pypsa

from .visualisation import DashboardError, EUROPE_SCOPE, _build_dashboard_data


DEFAULT_COMPARISON_OUTPUT = (
    Path(__file__).resolve().parent
    / "output"
    / "network_comparison_dashboard.html"
)
COMPARISON_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "comparison_dashboard_template.html"
)


def _comparison_value(current: Any, baseline: Any) -> dict[str, float | None]:
    """Return finite current/baseline values plus absolute and percentage deltas."""

    def finite(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    current_value = finite(current)
    baseline_value = finite(baseline)
    delta = (
        current_value - baseline_value
        if current_value is not None and baseline_value is not None
        else None
    )
    pct_delta = (
        delta / abs(baseline_value) * 100.0
        if delta is not None and baseline_value not in (None, 0.0)
        else None
    )
    return {
        "current": current_value,
        "baseline": baseline_value,
        "delta": delta,
        "pct_delta": pct_delta,
    }


def _carrier_comparison(
    current: dict[str, Any], baseline: dict[str, Any]
) -> list[dict[str, Any]]:
    current_rows = {
        row["carrier"]: row for row in current["generation"]["carriers"]
    }
    baseline_rows = {
        row["carrier"]: row for row in baseline["generation"]["carriers"]
    }
    rows: list[dict[str, Any]] = []
    for carrier in sorted(set(current_rows) | set(baseline_rows)):
        current_row = current_rows.get(carrier, {})
        baseline_row = baseline_rows.get(carrier, {})
        rows.append(
            {
                "carrier": carrier,
                "label": current_row.get("label")
                or baseline_row.get("label")
                or carrier,
                "color": current_row.get("color")
                or baseline_row.get("color")
                or "#7d8aa0",
                "capacity_gw": _comparison_value(
                    current_row.get("capacity_gw", 0.0),
                    baseline_row.get("capacity_gw", 0.0),
                ),
                "energy_twh": _comparison_value(
                    current_row.get("energy_twh", 0.0),
                    baseline_row.get("energy_twh", 0.0),
                ),
                "capacity_factor_pct": _comparison_value(
                    current_row.get("capacity_factor_pct"),
                    baseline_row.get("capacity_factor_pct"),
                ),
            }
        )
    rows.sort(
        key=lambda row: max(
            abs(row["energy_twh"]["current"] or 0.0),
            abs(row["energy_twh"]["baseline"] or 0.0),
        ),
        reverse=True,
    )
    return rows


def _flow_comparison(
    current: dict[str, Any], baseline: dict[str, Any]
) -> list[dict[str, Any]]:
    current_flow = current["flows"]
    baseline_flow = baseline["flows"]
    if current_flow["mode"] != baseline_flow["mode"]:
        raise DashboardError("The comparison scopes use incompatible flow modes.")

    if current_flow["mode"] == "zonal":
        current_rows = {row["zone"]: row for row in current_flow["neighbors"]}
        baseline_rows = {row["zone"]: row for row in baseline_flow["neighbors"]}
        result = []
        for zone in sorted(set(current_rows) | set(baseline_rows)):
            current_row = current_rows.get(zone, {})
            baseline_row = baseline_rows.get(zone, {})
            result.append(
                {
                    "label": zone,
                    "net_import_twh": _comparison_value(
                        current_row.get("net_import_twh", 0.0),
                        baseline_row.get("net_import_twh", 0.0),
                    ),
                    "imports_twh": _comparison_value(
                        current_row.get("imports_twh", 0.0),
                        baseline_row.get("imports_twh", 0.0),
                    ),
                    "exports_twh": _comparison_value(
                        current_row.get("exports_twh", 0.0),
                        baseline_row.get("exports_twh", 0.0),
                    ),
                }
            )
        result.sort(
            key=lambda row: max(
                abs(row["net_import_twh"]["current"] or 0.0),
                abs(row["net_import_twh"]["baseline"] or 0.0),
            ),
            reverse=True,
        )
        return result

    current_rows = {
        row["corridor"]: row for row in current_flow["corridors"]
    }
    baseline_rows = {
        row["corridor"]: row for row in baseline_flow["corridors"]
    }
    result = []
    for corridor in sorted(set(current_rows) | set(baseline_rows)):
        current_row = current_rows.get(corridor, {})
        baseline_row = baseline_rows.get(corridor, {})
        result.append(
            {
                "label": corridor,
                "throughput_twh": _comparison_value(
                    current_row.get("throughput_twh", 0.0),
                    baseline_row.get("throughput_twh", 0.0),
                ),
                "losses_twh": _comparison_value(
                    current_row.get("losses_twh", 0.0),
                    baseline_row.get("losses_twh", 0.0),
                ),
            }
        )
    result.sort(
        key=lambda row: max(
            abs(row["throughput_twh"]["current"] or 0.0),
            abs(row["throughput_twh"]["baseline"] or 0.0),
        ),
        reverse=True,
    )
    return result


def _zone_comparison(
    current: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    current_battery = current.get("battery") or {}
    baseline_battery = baseline.get("battery") or {}
    current_flow = current["flows"]
    baseline_flow = baseline["flows"]

    summary = {
        "mean_price_eur_mwh": _comparison_value(
            current["price"]["mean"], baseline["price"]["mean"]
        ),
        "price_std_eur_mwh": _comparison_value(
            current["price"]["std"], baseline["price"]["std"]
        ),
        "p05_eur_mwh": _comparison_value(
            current["price"]["p05"], baseline["price"]["p05"]
        ),
        "p95_eur_mwh": _comparison_value(
            current["price"]["p95"], baseline["price"]["p95"]
        ),
        "negative_hours": _comparison_value(
            current["price"]["negative_hours"],
            baseline["price"]["negative_hours"],
        ),
        "daily_spread_eur_mwh": _comparison_value(
            current["price"]["average_daily_spread"],
            baseline["price"]["average_daily_spread"],
        ),
        "tb4_spread_eur_mwh": _comparison_value(
            current["price"]["tb"]["tb4"]["spread_eur_mwh"],
            baseline["price"]["tb"]["tb4"]["spread_eur_mwh"],
        ),
        "demand_twh": _comparison_value(
            current["demand"]["annual_twh"], baseline["demand"]["annual_twh"]
        ),
        "generation_twh": _comparison_value(
            current["generation"]["total_generation_twh"],
            baseline["generation"]["total_generation_twh"],
        ),
        "renewable_share_pct": _comparison_value(
            current["generation"]["renewable_share_pct"],
            baseline["generation"]["renewable_share_pct"],
        ),
        "battery_power_gw": _comparison_value(
            current_battery.get("power_gw", 0.0),
            baseline_battery.get("power_gw", 0.0),
        ),
        "battery_energy_gwh": _comparison_value(
            current_battery.get("energy_gwh", 0.0),
            baseline_battery.get("energy_gwh", 0.0),
        ),
        "battery_cycles": _comparison_value(
            current_battery.get("equivalent_cycles"),
            baseline_battery.get("equivalent_cycles"),
        ),
        "battery_gross_revenue_meur": _comparison_value(
            current_battery.get("gross_revenue_meur"),
            baseline_battery.get("gross_revenue_meur"),
        ),
    }
    if current_flow["mode"] == "zonal" and baseline_flow["mode"] == "zonal":
        summary.update(
            {
                "net_import_twh": _comparison_value(
                    current_flow["net_import_twh"], baseline_flow["net_import_twh"]
                ),
                "imports_twh": _comparison_value(
                    current_flow["imports_twh"], baseline_flow["imports_twh"]
                ),
                "exports_twh": _comparison_value(
                    current_flow["exports_twh"], baseline_flow["exports_twh"]
                ),
            }
        )
    elif current_flow["mode"] == "internal" and baseline_flow["mode"] == "internal":
        summary.update(
            {
                "internal_transfer_twh": _comparison_value(
                    current_flow["internal_transfer_twh"],
                    baseline_flow["internal_transfer_twh"],
                ),
                "transmission_losses_twh": _comparison_value(
                    current_flow["transmission_losses_twh"],
                    baseline_flow["transmission_losses_twh"],
                ),
                "active_corridors": _comparison_value(
                    current_flow["active_corridors"],
                    baseline_flow["active_corridors"],
                ),
            }
        )
    else:
        raise DashboardError("The comparison scopes use incompatible flow modes.")

    return {
        "summary": summary,
        "carriers": _carrier_comparison(current, baseline),
        "flows": _flow_comparison(current, baseline),
    }


def _build_comparison_data(
    current_network: pypsa.Network,
    baseline_network: pypsa.Network,
    current_source: Path,
    baseline_source: Path,
    *,
    default_zone: str | None = None,
    title: str | None = None,
    current_label: str = "Latest calibration",
    baseline_label: str = "Old baseline",
) -> dict[str, Any]:
    """Build the serializable payload used by the comparison dashboard."""

    current_data = _build_dashboard_data(
        current_network, current_source, default_zone=default_zone
    )
    baseline_data = _build_dashboard_data(
        baseline_network, baseline_source, default_zone=default_zone
    )
    if current_data["timestamps"] != baseline_data["timestamps"]:
        raise DashboardError(
            "Comparison networks must use identical snapshots in the same order."
        )
    if list(current_data["zones"]) != list(baseline_data["zones"]):
        raise DashboardError(
            "Comparison networks must contain identical zones in the same order."
        )

    clean_current_label = current_label.strip()
    clean_baseline_label = baseline_label.strip()
    if not clean_current_label or not clean_baseline_label:
        raise DashboardError("Comparison model labels must not be empty.")

    dashboard_title = (
        title.strip()
        if title and title.strip()
        else "Latest Calibration vs Old Baseline"
    )
    zones = {
        zone: _zone_comparison(
            current_data["zones"][zone], baseline_data["zones"][zone]
        )
        for zone in current_data["zones"]
    }
    return {
        "schema_version": 1,
        "title": dashboard_title,
        "default_zone": current_data["default_zone"],
        "timestamps": current_data["timestamps"],
        "labels": {
            "current": clean_current_label,
            "baseline": clean_baseline_label,
        },
        "models": {
            "current": current_data,
            "baseline": baseline_data,
        },
        "comparison": {
            "system_objective_eur": _comparison_value(
                current_data["network"]["objective"],
                baseline_data["network"]["objective"],
            ),
            "zones": zones,
        },
    }


def _resolved_nc_path(path: str | Path, model_name: str) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"{model_name} solved network not found: {source}")
    if not source.is_file():
        raise DashboardError(f"{model_name} input path is not a file: {source}")
    if source.suffix.lower() != ".nc":
        raise DashboardError(
            f"Expected a .nc file for {model_name.lower()}, received: {source.name}"
        )
    return source


def _load_network(source: Path) -> pypsa.Network:
    network = pypsa.Network()
    try:
        network.import_from_netcdf(str(source))
    except Exception as exc:  # PyPSA raises backend-specific exceptions.
        raise DashboardError(
            f"Could not read PyPSA network '{source}': {exc}"
        ) from exc
    return network


def generate_comparison_dashboard(
    current_path: str | Path,
    baseline_path: str | Path,
    output_path: str | Path | None = None,
    default_zone: str | None = None,
    title: str | None = None,
    current_label: str = "Latest calibration",
    baseline_label: str = "Old baseline",
) -> Path:
    """Generate a standalone HTML comparison of two solved PyPSA networks."""

    current_source = _resolved_nc_path(current_path, "Current")
    baseline_source = _resolved_nc_path(baseline_path, "Baseline")
    destination = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else DEFAULT_COMPARISON_OUTPUT.resolve()
    )
    if destination.suffix.lower() != ".html":
        raise DashboardError(f"Output path must end with .html: {destination}")

    data = _build_comparison_data(
        _load_network(current_source),
        _load_network(baseline_source),
        current_source,
        baseline_source,
        default_zone=default_zone,
        title=title,
        current_label=current_label,
        baseline_label=baseline_label,
    )
    try:
        template = COMPARISON_TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise DashboardError(
            f"Comparison dashboard template could not be read: {COMPARISON_TEMPLATE_PATH}"
        ) from exc

    payload = json.dumps(
        data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    payload = payload.replace("<", "\\u003c").replace("&", "\\u0026")
    rendered = template.replace(
        "__DASHBOARD_TITLE__", html.escape(data["title"])
    ).replace("__DASHBOARD_DATA__", payload)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    temporary.replace(destination)
    return destination


__all__ = ["generate_comparison_dashboard"]
