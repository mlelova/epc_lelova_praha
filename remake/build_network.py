"""Thin single-network wrapper around :mod:`scenarios.build_network`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .errors import OverrideValidationError


@dataclass(frozen=True)
class BuildConfig:
    built_network_path: Path
    ntc_scale: float = 1.0
    load_scale: float = 1.0
    battery_scale: float = 1.0
    battery_override_df: pd.DataFrame | None = None
    nuclear_profile_df: pd.DataFrame | None = None
    ntc_override_df: pd.DataFrame | None = None
    generator_availability_df: pd.DataFrame | None = None
    battery_extendable: bool = False
    slack_cost: float | None = 3_000.0


def build_single_network(data: dict, config: BuildConfig):
    """Build, consistency-check, and export one unsolved network."""
    from scenarios.build_network import build_network

    config.built_network_path.parent.mkdir(parents=True, exist_ok=True)
    network = build_network(
        data=data,
        ntc_scale=config.ntc_scale,
        load_scale=config.load_scale,
        battery_scale=config.battery_scale,
        battery_override=config.battery_override_df,
        nuclear_p_max_pu=config.nuclear_profile_df,
        ntc_override=config.ntc_override_df,
        battery_extendable=config.battery_extendable,
        slack_cost=config.slack_cost,
        output_path=None,
    )
    if config.generator_availability_df is not None:
        apply_generator_availability(network, config.generator_availability_df)
    network.consistency_check()
    network.export_to_netcdf(str(config.built_network_path))
    return network


def apply_generator_availability(network, override: pd.DataFrame) -> None:
    """Replace built generator p_max_pu profiles with validated hourly limits."""
    applied = 0
    for (bus, carrier), group in override.groupby(
        ["bus", "index_carrier"], sort=False
    ):
        generator = f"{bus}-{carrier}"
        if generator not in network.generators.index:
            raise OverrideValidationError(
                f"Generator availability target is not present in built network: {generator}"
            )
        ordered = group.sort_values("snapshot")
        timestamps = pd.DatetimeIndex(ordered["snapshot"])
        if not timestamps.equals(network.snapshots):
            raise OverrideValidationError(
                f"Generator availability timestamps do not match network for {generator}"
            )
        network.generators_t.p_max_pu[generator] = ordered["p_max_pu"].to_numpy()
        applied += 1
    print(f"  Generator availability overrides: {applied} applied")
