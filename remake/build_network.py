"""Thin single-network wrapper around :mod:`scenarios.build_network`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class BuildConfig:
    built_network_path: Path
    ntc_scale: float = 1.0
    load_scale: float = 1.0
    battery_scale: float = 1.0
    battery_override_df: pd.DataFrame | None = None
    nuclear_profile_df: pd.DataFrame | None = None
    ntc_override_df: pd.DataFrame | None = None
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
    network.consistency_check()
    network.export_to_netcdf(str(config.built_network_path))
    return network
