"""
solve_scenarios.py
==================
Solves all networks listed in manifest.csv using Gurobi (WLS) and saves
results to solved_networks/.  Supports parallel solving via multiprocessing.Pool (maxtasksperchild=1)
so each worker restarts after every solve, fully releasing memory.

Run on the server from the project root:
    python scenarios/solve_scenarios.py --networks-dir networks_core --workers 6
    python scenarios/solve_scenarios.py --networks-dir networks_core --tag bat2x_gas22.68_co2_113.4_cy2009_dur1x
    python scenarios/solve_scenarios.py --networks-dir networks_core --force

Options:
    --networks-dir  DIR   Directory with input .nc files and manifest.csv
    --output-dir    DIR   Directory for solved .nc files (default: solved_networks)
    --tag           TAG   Solve only the scenario with this tag
    --workers       N     Number of parallel Gurobi processes (default: 1)
    --threads       N     Gurobi threads per worker (default: 2)
    --force               Re-solve even if output file already exists
"""

from __future__ import annotations

import pandas as pd

pd.options.future.infer_string = (
    False  # prevent ArrowStringArray in PyPSA 1.0.7 networks on newer pandas
)

import argparse
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pandas as pd
import pypsa

ROOT = Path(__file__).resolve().parent.parent


# ===========================================================================
# Single-network solver
# ===========================================================================


def solve_network(
    network_path: Path,
    output_path: Path,
    threads: int = 2,
) -> dict:
    """Load, solve, and export one network. Returns a result dict."""
    result = {
        "status": "unknown",
        "termination_condition": "unknown",
        "objective": None,
        "slack_mwh": None,
        "solve_time_s": None,
    }

    n = pypsa.Network()
    n.import_from_netcdf(str(network_path))

    solver_options = {
        "threads": threads,
        "FeasibilityTol": 1e-5,
        "OptimalityTol": 1e-5,
        "LogToConsole": 0,
        "LogFile": str(output_path.parent / f"{output_path.stem}_gurobi.log"),
    }

    t0 = time.time()
    opt_status, opt_condition = n.optimize(
        solver_name="gurobi",
        io_api="direct",
        solver_options=solver_options,
    )
    result["solve_time_s"] = round(time.time() - t0, 1)
    result["status"] = str(opt_status)
    result["termination_condition"] = str(opt_condition)

    try:
        result["objective"] = float(n.objective)
    except Exception:
        result["objective"] = None

    if "slack" in n.generators.carrier.values:
        slack_gens = n.generators[n.generators.carrier == "slack"].index
        if slack_gens.isin(n.generators_t.p.columns).any():
            slack_p = n.generators_t.p[
                slack_gens.intersection(n.generators_t.p.columns)
            ]
            result["slack_mwh"] = round(float(slack_p.sum().sum()), 1)
        else:
            result["slack_mwh"] = 0.0

    if str(opt_condition).lower() in ("optimal", "suboptimal"):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        n.export_to_netcdf(str(output_path))
        result["status"] = "solved"
    else:
        result["status"] = f"failed:{opt_condition}"

    return result


# ===========================================================================
# Parallel worker (runs in subprocess)
# ===========================================================================


def _worker(task: dict) -> dict:
    """Solve one network in a worker process. Returns enriched result dict."""
    # Suppress PyPSA/pypsa output in workers
    sys.stdout = open(os.devnull, "w")
    try:
        result = solve_network(
            network_path=Path(task["net_path"]),
            output_path=Path(task["out_path"]),
            threads=task["threads"],
        )
    except Exception as exc:
        result = {
            "status": f"error:{exc}",
            "termination_condition": "error",
            "objective": None,
            "slack_mwh": None,
            "solve_time_s": None,
        }
    finally:
        sys.stdout.close()
        sys.stdout = sys.__stdout__

    result["tag"] = task["tag"]
    result["matrix"] = task.get("matrix", "")
    result["battery_scale"] = task.get("battery_scale", "")
    result["gas_price"] = task.get("gas_price", "")
    result["co2_price"] = task.get("co2_price", "")
    result["climate_year"] = task.get("climate_year", "")
    result["battery_duration"] = task.get("battery_duration", "")
    result["battery_extendable"] = task.get("battery_extendable", "")
    return result


# ===========================================================================
# Results CSV helper
# ===========================================================================


def _record(
    results_df: pd.DataFrame, path: Path, row: pd.Series, result: dict
) -> pd.DataFrame:
    record = {**dict(row), **result}
    new_row = pd.DataFrame([record])
    if "tag" in results_df.columns and record.get("tag") in results_df["tag"].values:
        results_df.loc[results_df["tag"] == record["tag"], list(result.keys())] = list(
            result.values()
        )
    else:
        results_df = pd.concat([results_df, new_row], ignore_index=True)
    results_df.to_csv(path, index=False)
    return results_df


# ===========================================================================
# Main runner
# ===========================================================================


def run(
    networks_dir: Path,
    output_dir: Path,
    tag_filter: str | None,
    threads: int,
    workers: int,
    force: bool,
) -> None:
    manifest_path = networks_dir / "manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    manifest = pd.read_csv(manifest_path)
    if tag_filter:
        manifest = manifest[manifest["tag"] == tag_filter]
    if manifest.empty:
        print("No matching scenarios found.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    results_path = output_dir / "solve_results.csv"
    results_df = pd.read_csv(results_path) if results_path.exists() else pd.DataFrame()

    total = len(manifest)
    solved = skipped = failed = 0
    t_start = time.time()

    print(f"\nSolving {total} networks")
    print(f"  Input:    {networks_dir}")
    print(f"  Output:   {output_dir}")
    print(f"  Workers:  {workers}  |  Gurobi threads/worker: {threads}")
    print()

    # Separate into skip and build lists
    to_skip = []
    to_build = []
    for _, row in manifest.iterrows():
        tag = row["tag"]
        net_path = networks_dir / f"{tag}.nc"
        out_path = output_dir / f"{tag}.nc"

        if not net_path.exists():
            print(f"  MISSING  {tag}")
            results_df = _record(
                results_df, results_path, row, {"status": "missing_input"}
            )
            failed += 1
            continue

        if out_path.exists() and not force:
            to_skip.append((row, out_path))
        else:
            to_build.append((row, net_path, out_path))

    # Report skips
    for row, _ in to_skip:
        skipped += 1
        print(f"  SKIP     {row['tag']}")

    if not to_build:
        print(f"\nAll {skipped} already solved.")
        return

    # Build task list for workers
    tasks = [
        {
            "tag": row["tag"],
            "net_path": str(net_path),
            "out_path": str(out_path),
            "threads": threads,
            "matrix": row.get("matrix", ""),
            "battery_scale": row.get("battery_scale", ""),
            "gas_price": row.get("gas_price", ""),
            "co2_price": row.get("co2_price", ""),
            "climate_year": row.get("climate_year", ""),
            "battery_duration": row.get("battery_duration", ""),
            "battery_extendable": row.get("battery_extendable", ""),
        }
        for row, net_path, out_path in to_build
    ]

    done = skipped  # offset counter
    total_done = skipped

    with multiprocessing.Pool(processes=workers, maxtasksperchild=1) as pool:
        async_results = [(task, pool.apply_async(_worker, (task,))) for task in tasks]
        for task, ar in async_results:
            total_done += 1
            try:
                result = ar.get()
            except Exception as exc:
                result = {"tag": task["tag"], "status": f"error:{exc}"}
                failed += 1
                print(f"  [{total_done}/{total}] ERROR    {task['tag']}: {exc}")
                continue

            # Find original manifest row for _record
            row = manifest[manifest["tag"] == result["tag"]].iloc[0]
            results_df = _record(results_df, results_path, row, result)

            status = result["status"]
            t = result.get("solve_time_s", "?")
            obj = result.get("objective")
            slack = result.get("slack_mwh")

            if status == "solved":
                solved += 1
                slack_warn = (
                    f"  ⚠ slack={slack:.0f} MWh" if slack and slack > 1000 else ""
                )
                obj_str = f"  obj={obj:.0f}" if obj is not None else ""
                print(
                    f"  [{total_done}/{total}] OK       {result['tag']}  {t}s{obj_str}{slack_warn}"
                )
            else:
                failed += 1
                print(
                    f"  [{total_done}/{total}] FAILED   {result['tag']}  status={status}"
                )

    elapsed = time.time() - t_start
    print(f"\n{'='*60}")
    print(
        f"Done in {elapsed/60:.1f} min  —  {solved} solved, {skipped} skipped, {failed} failed"
    )
    print(f"Results: {results_path}")


# ===========================================================================
# CLI
# ===========================================================================

if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    parser = argparse.ArgumentParser(
        description="Solve sensitivity-analysis networks with Gurobi (parallel)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scenarios/solve_scenarios.py --networks-dir scenarios/networks_core --workers 6
  python scenarios/solve_scenarios.py --networks-dir scenarios/networks_core --workers 6 --threads 3
  python scenarios/solve_scenarios.py --networks-dir scenarios/networks_core --tag bat1x_gas22.68_co2_113.4_cy2009_dur1x
  python scenarios/solve_scenarios.py --networks-dir scenarios/networks_core --workers 6 --force
        """,
    )
    parser.add_argument(
        "--networks-dir",
        type=Path,
        default=ROOT / "scenarios" / "networks_core",
        help="Directory with input .nc files and manifest.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "solved_networks",
        help="Directory for solved .nc files (default: solved_networks/)",
    )
    parser.add_argument("--tag", default=None, help="Solve only this scenario tag")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel Gurobi processes (default: 1)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=2,
        help="Gurobi threads per worker process (default: 2)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-solve even if output file already exists",
    )
    args = parser.parse_args()

    run(
        networks_dir=args.networks_dir,
        output_dir=args.output_dir,
        tag_filter=args.tag,
        threads=args.threads,
        workers=args.workers,
        force=args.force,
    )
