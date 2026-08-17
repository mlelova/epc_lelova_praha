"""
run_scenarios.py
================
Generates all sensitivity-analysis network files from the parameter grid
defined in sensitivity_analysis.md.

Networks are saved to scenarios/networks/ as unsolved .nc files, ready for
Gurobi optimisation on the server.  A manifest CSV is written to
scenarios/networks/manifest.csv and updated after every build.

Run from the project root:
    python scenarios/run_scenarios.py              # all matrices
    python scenarios/run_scenarios.py --dry-run    # print plan, build nothing
    python scenarios/run_scenarios.py --matrix core      # core dispatch only (432 runs)
    python scenarios/run_scenarios.py --matrix nuclear   # nuclear sensitivity (18 runs)
    python scenarios/run_scenarios.py --matrix demand    # demand sensitivity (18 runs)
    python scenarios/run_scenarios.py --matrix ntc       # NTC sensitivity (18 runs)
    python scenarios/run_scenarios.py --matrix invest    # investment runs (36 runs)

Matrices:
    core      bat_scale(4) × bat_duration(4) × gas_price(3) × co2_price(3) × climate_year(3) = 432 runs
              bat_scale:    1×, 2×, 4×, 8×  (log spacing)
              bat_duration: 1×, 2×, 3×, 4×  (cap 8 h per bus)
              gas_price:    18, 22.68, 35 EUR/MWh_th
              co2_price:    80, 113.4, 140 EUR/tCO₂
              climate_year: CY2003, CY2009, CY2012

    nuclear   battery_scale(4) × nuclear_scale(3),    baseline gas/co2/cy       =  12 runs
    demand    battery_scale(4) × demand_scale(3),     baseline gas/co2/cy       =  12 runs
    ntc       battery_scale(4) × ntc_scale(3),        baseline gas/co2/cy       =  12 runs
    invest    gas_price(3) × co2_price(3) × climate_year(3), battery extendable =  27 runs

Performance note: PECD + hydro + demand loading is the expensive step (~2–5 min
per climate year).  Scenarios are batched by climate year so that load happens
once per CY, not once per scenario.
"""

from __future__ import annotations

import argparse
import copy
import io
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import pypsa

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_network import (
    _aggregate_storage,
    build_network,
    STORAGE_CARRIERS,
)  # noqa: E402
from load_network_data import _apply_gas_co2_prices, load_network_data  # noqa: E402

DATA_DIR = ROOT / "data" / "open-tyndp"
TYNDP_DIR = ROOT / "data" / "tyndp2024"


def _out_dir(matrix: str) -> Path:
    """Return the output directory for a given matrix name."""
    return ROOT / "scenarios" / f"networks_{matrix}"


# ===========================================================================
# Parameter grids — values from sensitivity_analysis.md
# ===========================================================================

# Core matrix — 5D full factorial dispatch
BATTERY_SCALES = [1, 2, 4, 8]  # log spacing (×)
BATTERY_DURATIONS = [1, 2, 3, 4]  # multipliers on per-bus PEMMDB max_hours (cap 8 h)
GAS_PRICES = [18, 22.68, 35]  # EUR/MWh_th
CO2_PRICES = [80, 113.4, 140]  # EUR/tCO₂
CLIMATE_YEARS = [2003, 2009, 2012]

# Baseline values used in one-at-a-time sensitivity matrices
BASE_GAS = 22.68  # EUR/MWh_th  — TYNDP 2024 NT reference
BASE_CO2 = 113.4  # EUR/tCO₂   — TYNDP 2024 NT reference
BASE_CY = 2009
NUCLEAR_SCALES = [0.65, 0.80, 0.90]  # p_max_pu multiplier
DEMAND_SCALES = [1.00, 1.10, 1.15]  # load multiplier
NTC_SCALES = [0.80, 1.00, 1.20]  # interconnector p_nom multiplier

# Fixed
LOAD_SCALE_BASE = 1.0
NTC_SCALE_BASE = 1.0


# ===========================================================================
# Helpers
# ===========================================================================


def _tag(
    battery_scale: int | float,
    gas_price: int | float,
    climate_year: int,
    co2_price: float,
    battery_duration: float | None = None,
    nuclear_scale: float | None = None,
    demand_scale: float | None = None,
    ntc_scale: float | None = None,
    extendable: bool = False,
) -> str:
    """Build a short, filesystem-safe scenario identifier."""
    parts = [
        f"bat{battery_scale}x",
        f"gas{gas_price:g}",
        f"co2_{co2_price:g}",
        f"cy{climate_year}",
    ]
    if battery_duration is not None:
        parts.append(f"dur{int(battery_duration)}x")
    if nuclear_scale is not None:
        parts.append(f"nuc{nuclear_scale:.2f}".replace(".", "p"))
    if demand_scale is not None:
        parts.append(f"dem{demand_scale:.2f}".replace(".", "p"))
    if ntc_scale is not None:
        parts.append(f"ntc{ntc_scale:.2f}".replace(".", "p"))
    if extendable:
        parts.append("invest")
    return "_".join(parts)


def _battery_override_df(
    capacities: pd.DataFrame,
    battery_scale: float,
    battery_duration: float,
) -> pd.DataFrame:
    """
    Build a battery_override DataFrame for build_network() using a duration multiplier.

    For each bus:
      - duration_h = min(base_max_hours * battery_duration, 8.0)  if base_max_hours < 8 h
      - duration_h = base_max_hours                               if base_max_hours >= 8 h
    This preserves per-country heterogeneity while testing longer-duration deployment.
    """
    storage_raw = capacities[capacities["pypsa_carrier"].isin(STORAGE_CARRIERS)].copy()
    agg = _aggregate_storage(storage_raw)
    bat = agg[agg["carrier"] == "battery"].copy()
    if bat.empty:
        return pd.DataFrame(columns=["bus", "p_nom_mw", "duration_h"])
    duration_h = [
        min(mh * battery_duration, 8.0) if mh < 8.0 else mh
        for mh in bat["max_hours"].values
    ]
    return pd.DataFrame(
        {
            "bus": bat["bus"].values,
            "p_nom_mw": bat["p_nom"].values * battery_scale,
            "duration_h": duration_h,
        }
    )


def _build_one(
    base_data: dict,
    gas_price: float,
    co2_price: float,
    battery_scale: float,
    load_scale: float,
    ntc_scale: float,
    battery_duration: float | None,
    nuclear_scale: float,
    battery_extendable: bool,
    out_path: Path,
) -> "pypsa.Network":
    """Apply price adjustments, build network, export to out_path. Returns network object."""

    # Price-adjusted data (cheap copy — only technologies DataFrame differs)
    data = copy.copy(base_data)
    data["technologies"] = _apply_gas_co2_prices(
        base_data["technologies"].copy(), gas_price, co2_price
    )

    # Optional: battery duration override
    bat_override = None
    bat_scale_arg = battery_scale
    if battery_duration is not None:
        bat_override = _battery_override_df(
            data["capacities"], battery_scale, battery_duration
        )
        bat_scale_arg = 1.0  # p_nom already baked into override

    # Optional: nuclear availability scaling
    nuc_profiles = None
    if nuclear_scale != 1.0:
        nuc_profiles = (data["nuclear_profiles"] * nuclear_scale).clip(0.0, 1.0)

    return build_network(
        data=data,
        battery_scale=bat_scale_arg,
        battery_override=bat_override,
        nuclear_p_max_pu=nuc_profiles,
        load_scale=load_scale,
        ntc_scale=ntc_scale,
        battery_extendable=battery_extendable,
        output_path=str(out_path),
    )


def _consistency_check(n: "pypsa.Network") -> str:
    """Run consistency_check() on network object. Returns warning string or ''."""
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setLevel(logging.WARNING)
    pypsa_logger = logging.getLogger("pypsa")
    pypsa_logger.addHandler(handler)
    try:
        n.consistency_check()
    finally:
        pypsa_logger.removeHandler(handler)
    return log_capture.getvalue().strip().replace("\n", " | ")


# ===========================================================================
# Parallel worker
# ===========================================================================

_worker_base_data: dict | None = None


def _worker_init(base_data: dict) -> None:
    """Called once per worker process — stores base_data in global state."""
    global _worker_base_data
    _worker_base_data = base_data


def _worker_task(task: dict) -> dict:
    """Build one network in a worker process. Returns result dict."""
    # Suppress verbose build output from workers
    sys.stdout = open(os.devnull, "w")
    try:
        out_path = Path(task["output_file"])
        n = _build_one(
            base_data=_worker_base_data,
            gas_price=task["gas_price"],
            co2_price=task["co2_price"],
            battery_scale=task["battery_scale"],
            load_scale=task["load_scale"],
            ntc_scale=task["ntc_scale"],
            battery_duration=task["battery_duration"],
            nuclear_scale=task["nuclear_scale"],
            battery_extendable=task["battery_extendable"],
            out_path=out_path,
        )
        warnings = _consistency_check(n)
        return {
            "tag": task["tag"],
            "status": "built_with_warnings" if warnings else "built",
            "consistency_warnings": warnings,
        }
    except Exception as exc:
        return {
            "tag": task["tag"],
            "status": f"error: {exc}",
            "consistency_warnings": "",
        }
    finally:
        sys.stdout.close()
        sys.stdout = sys.__stdout__


# ===========================================================================
# Scenario assembly
# ===========================================================================


def _assemble_scenarios(matrix_filter: str | None) -> list[dict]:
    """Return the full list of scenario parameter dicts."""
    scenarios: list[dict] = []

    def add(
        tag,
        climate_year,
        gas_price,
        co2_price,
        battery_scale,
        load_scale,
        ntc_scale,
        battery_duration,
        nuclear_scale,
        battery_extendable,
        matrix,
    ):
        scenarios.append(
            dict(
                tag=tag,
                climate_year=climate_year,
                gas_price=gas_price,
                co2_price=co2_price,
                battery_scale=battery_scale,
                load_scale=load_scale,
                ntc_scale=ntc_scale,
                battery_duration=battery_duration,
                nuclear_scale=nuclear_scale,
                battery_extendable=battery_extendable,
                matrix=matrix,
            )
        )

    # Core — 5D full factorial: bat_scale × bat_duration × gas × co2 × cy = 432 runs
    if matrix_filter in (None, "core"):
        for cy in CLIMATE_YEARS:
            for gas_p in GAS_PRICES:
                for co2_p in CO2_PRICES:
                    for bat_s in BATTERY_SCALES:
                        for dur in BATTERY_DURATIONS:
                            add(
                                tag=_tag(bat_s, gas_p, cy, co2_p, battery_duration=dur),
                                climate_year=cy,
                                gas_price=gas_p,
                                co2_price=co2_p,
                                battery_scale=bat_s,
                                load_scale=LOAD_SCALE_BASE,
                                ntc_scale=NTC_SCALE_BASE,
                                battery_duration=dur,
                                nuclear_scale=1.0,
                                battery_extendable=False,
                                matrix="core",
                            )

    # Nuclear sensitivity — bat × nuclear_scale at baseline = 18 runs
    if matrix_filter in (None, "nuclear"):
        for nuc in NUCLEAR_SCALES:
            for bat_s in BATTERY_SCALES:
                add(
                    tag=_tag(bat_s, BASE_GAS, BASE_CY, BASE_CO2, nuclear_scale=nuc),
                    climate_year=BASE_CY,
                    gas_price=BASE_GAS,
                    co2_price=BASE_CO2,
                    battery_scale=bat_s,
                    load_scale=LOAD_SCALE_BASE,
                    ntc_scale=NTC_SCALE_BASE,
                    battery_duration=None,
                    nuclear_scale=nuc,
                    battery_extendable=False,
                    matrix="nuclear",
                )

    # Demand sensitivity — bat × demand_scale at baseline = 18 runs
    if matrix_filter in (None, "demand"):
        for dem in DEMAND_SCALES:
            for bat_s in BATTERY_SCALES:
                add(
                    tag=_tag(bat_s, BASE_GAS, BASE_CY, BASE_CO2, demand_scale=dem),
                    climate_year=BASE_CY,
                    gas_price=BASE_GAS,
                    co2_price=BASE_CO2,
                    battery_scale=bat_s,
                    load_scale=dem,
                    ntc_scale=NTC_SCALE_BASE,
                    battery_duration=None,
                    nuclear_scale=1.0,
                    battery_extendable=False,
                    matrix="demand",
                )

    # NTC sensitivity — bat × ntc_scale at baseline = 18 runs
    if matrix_filter in (None, "ntc"):
        for ntc in NTC_SCALES:
            for bat_s in BATTERY_SCALES:
                add(
                    tag=_tag(bat_s, BASE_GAS, BASE_CY, BASE_CO2, ntc_scale=ntc),
                    climate_year=BASE_CY,
                    gas_price=BASE_GAS,
                    co2_price=BASE_CO2,
                    battery_scale=bat_s,
                    load_scale=LOAD_SCALE_BASE,
                    ntc_scale=ntc,
                    battery_duration=None,
                    nuclear_scale=1.0,
                    battery_extendable=False,
                    matrix="ntc",
                )

    # Invest — full factorial, solver sizes batteries: gas × co2 × cy = 27 runs
    if matrix_filter in (None, "invest"):
        for cy in CLIMATE_YEARS:
            for gas_p in GAS_PRICES:
                for co2_p in CO2_PRICES:
                    add(
                        tag=_tag(1, gas_p, cy, co2_p, extendable=True),
                        climate_year=cy,
                        gas_price=gas_p,
                        co2_price=co2_p,
                        battery_scale=1,
                        load_scale=LOAD_SCALE_BASE,
                        ntc_scale=NTC_SCALE_BASE,
                        battery_duration=None,
                        nuclear_scale=1.0,
                        battery_extendable=True,
                        matrix="invest",
                    )

    # Assign IDs and output paths; deduplicate tags
    seen_tags: set[str] = set()
    result = []
    for i, s in enumerate(scenarios):
        if s["tag"] in seen_tags:
            continue
        seen_tags.add(s["tag"])
        s["scenario_id"] = len(result)
        s["output_file"] = str(_out_dir(s["matrix"]) / f"{s['tag']}.nc")
        s["status"] = "pending"
        result.append(s)
    return result


# ===========================================================================
# Main builder
# ===========================================================================


def run(
    dry_run: bool = False,
    matrix_filter: str | None = None,
    remove_old: bool = False,
    n_workers: int = 4,
) -> None:
    scenarios = _assemble_scenarios(matrix_filter)
    manifest_df = pd.DataFrame(scenarios)

    # Create per-matrix output directories; optionally wipe them
    matrices_used = manifest_df["matrix"].unique()
    for matrix in matrices_used:
        d = _out_dir(matrix)
        if remove_old and not dry_run:
            import shutil

            if d.exists():
                shutil.rmtree(d)
                print(f"Removed {d}")
        d.mkdir(parents=True, exist_ok=True)

    # Write one manifest per matrix directory
    def _write_manifests(df: pd.DataFrame) -> None:
        for matrix, grp in df.groupby("matrix"):
            manifest_path = _out_dir(matrix) / "manifest.csv"
            grp.to_csv(manifest_path, index=False)

    # Summary
    print("\nScenario plan:")
    print(manifest_df.groupby("matrix").size().rename("count").to_string())
    print(f"Total: {len(scenarios)} scenarios")
    for matrix in matrices_used:
        print(f"  networks_{matrix}/  ->  {_out_dir(matrix)}")

    _write_manifests(manifest_df)

    if dry_run:
        print("\n[DRY RUN] Manifest written. No networks built.")
        return

    built = skipped = failed = 0
    total = len(scenarios)
    done = 0
    t_start = time.time()

    # Batch by climate year to load PECD/hydro/demand data only once per CY
    for cy in sorted({s["climate_year"] for s in scenarios}):
        cy_group = [s for s in scenarios if s["climate_year"] == cy]

        print(f"\n{'='*64}")
        print(f"Climate year {cy}  —  loading PECD + hydro + demand...")
        t_load = time.time()
        try:
            base_data = load_network_data(
                data_dir=DATA_DIR,
                tyndp_dir=TYNDP_DIR,
                climate_year=cy,
            )
        except Exception as exc:
            print(f"  ERROR loading CY{cy}: {exc}")
            for s in cy_group:
                s["status"] = f"load_error: {exc}"
            failed += len(cy_group)
            continue
        print(
            f"  Loaded in {time.time() - t_load:.0f}s  ({len(cy_group)} scenarios queued)"
        )

        # Skips (sequential — fast)
        to_skip = [s for s in cy_group if Path(s["output_file"]).exists()]
        to_build = [s for s in cy_group if not Path(s["output_file"]).exists()]

        for s in to_skip:
            done += 1
            print(f"  [{done}/{total}] SKIP  {s['tag']}")
            s["status"] = "skipped"
            s["consistency_warnings"] = ""
            skipped += 1
            manifest_df.loc[manifest_df["tag"] == s["tag"], "status"] = "skipped"
        if to_skip:
            _write_manifests(manifest_df)

        # Builds (parallel)
        if to_build:
            with ProcessPoolExecutor(
                max_workers=n_workers,
                initializer=_worker_init,
                initargs=(base_data,),
            ) as executor:
                futures = {executor.submit(_worker_task, s): s for s in to_build}
                for future in as_completed(futures):
                    s = futures[future]
                    result = future.result()
                    done += 1
                    s["status"] = result["status"]
                    s["consistency_warnings"] = result["consistency_warnings"]
                    if "error" in result["status"]:
                        failed += 1
                        print(
                            f"  [{done}/{total}] ERROR  {s['tag']}: {result['status']}"
                        )
                    elif result["status"] == "built_with_warnings":
                        built += 1
                        print(f"  [{done}/{total}] WARN   {s['tag']}")
                    else:
                        built += 1
                        print(f"  [{done}/{total}] OK     {s['tag']}")
                    manifest_df.loc[
                        manifest_df["tag"] == s["tag"],
                        ["status", "consistency_warnings"],
                    ] = [s["status"], s["consistency_warnings"]]
                    _write_manifests(manifest_df)

    elapsed = time.time() - t_start
    print(f"\n{'='*64}")
    print(
        f"Finished in {elapsed/60:.1f} min  —  "
        f"{built} built, {skipped} skipped, {failed} failed"
    )
    for matrix in matrices_used:
        print(f"Manifest: {_out_dir(matrix) / 'manifest.csv'}")


# ===========================================================================
# CLI
# ===========================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build TYNDP 2030 sensitivity-analysis networks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scenarios/run_scenarios.py --dry-run
  python scenarios/run_scenarios.py --matrix core
  python scenarios/run_scenarios.py --matrix duration
  python scenarios/run_scenarios.py --matrix nuclear
  python scenarios/run_scenarios.py --matrix demand
  python scenarios/run_scenarios.py --matrix ntc
  python scenarios/run_scenarios.py --matrix invest
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print scenario plan and write manifest without building",
    )
    parser.add_argument(
        "--matrix",
        choices=["core", "nuclear", "demand", "ntc", "invest"],
        default=None,
        help="Build only a specific matrix (default: all matrices)",
    )
    parser.add_argument(
        "--remove-old",
        action="store_true",
        help="Delete existing networks_{matrix}/ directories before building",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel worker processes (default: 4)",
    )
    args = parser.parse_args()

    run(
        dry_run=args.dry_run,
        matrix_filter=args.matrix,
        remove_old=args.remove_old,
        n_workers=args.workers,
    )
