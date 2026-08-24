"""Command-line interface for one-off company-data forecasts."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ._helpers import git_provenance, utc_now, write_json
from .build_network import BuildConfig, build_single_network
from .company_availability import extract_company_availability
from .company_capacities import extract_company_capacities
from .generation_comparison import (
    compare_generation,
    read_production_reference,
)
from .input_data import read_table
from .load_network import (
    OverrideValidationError,
    load_remake_data,
    read_battery_override,
    read_generator_availability_override,
    read_ntc_override,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "data" / "open-tyndp"
DEFAULT_OUTPUT_DIR = ROOT / "remake" / "output"
DEFAULT_COMPANY_OUTPUT_DIR = ROOT / "company_data" / "processed"
DEFAULT_BASE_CAPACITIES = DEFAULT_DATA_DIR / "pemmdb_capacities_2030_grouped.csv"
DEFAULT_CAPACITY_OVERRIDE = (
    DEFAULT_COMPANY_OUTPUT_DIR / "capacity_override_de00_2030.csv"
)
TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _non_negative(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite non-negative number")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _year(value: str) -> int:
    parsed = int(value)
    if parsed < 1900 or parsed > 2200:
        raise argparse.ArgumentTypeError("must be between 1900 and 2200")
    return parsed


def _climate_year(value: str) -> int:
    parsed = int(value)
    if not 1982 <= parsed <= 2017:
        raise argparse.ArgumentTypeError("must be between 1982 and 2017")
    return parsed


def _tag(value: str) -> str:
    if not TAG_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "must start with an alphanumeric character and contain only letters, "
            "numbers, '.', '_' or '-'"
        )
    return value


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tag", required=True, type=_tag, help="Unique run tag")
    parser.add_argument("--climate-year", type=_climate_year, default=2009)
    parser.add_argument("--gas-price", type=_non_negative, help="EUR/MWh_th")
    parser.add_argument("--coal-price", type=_non_negative, help="EUR/MWh_th")
    parser.add_argument("--co2-price", type=_non_negative, help="EUR/tCO2")
    parser.add_argument("--battery-scale", type=_non_negative, default=1.0)
    parser.add_argument("--ntc-scale", type=_non_negative, default=1.0)
    parser.add_argument("--load-scale", type=_non_negative, default=1.0)
    parser.add_argument("--battery-override", type=_path)
    parser.add_argument("--capacity-override", type=_path)
    parser.add_argument("--technology-override", type=_path)
    parser.add_argument("--ntc-override", type=_path)
    parser.add_argument("--nuclear-profile-override", type=_path)
    parser.add_argument("--demand-override", type=_path)
    parser.add_argument("--vre-override", type=_path)
    parser.add_argument("--generator-availability-override", type=_path)
    parser.add_argument("--battery-extendable", action="store_true")
    parser.add_argument(
        "--slack-cost",
        type=_non_negative,
        default=3000.0,
        help="Slack generator marginal cost in EUR/MWh",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--build-only", action="store_true", help="Build but do not solve (default)")
    mode.add_argument("--solve", action="store_true", help="Solve the built network with Gurobi")
    parser.add_argument("--threads", type=_positive_int, default=2)
    parser.add_argument(
        "--input-dir",
        "--data-dir",
        dest="data_dir",
        type=_path,
        default=DEFAULT_DATA_DIR,
        help="CSV/Excel base-input directory (default: data/open-tyndp)",
    )
    parser.add_argument("--output-dir", type=_path, default=DEFAULT_OUTPUT_DIR)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m remake",
        description="Build and optionally solve one forecast using company-data overrides.",
    )
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Build and optionally solve one network")
    _add_run_arguments(run_parser)

    compare = subparsers.add_parser("compare", help="Compare solved zonal prices with actuals")
    compare.add_argument("--solved", required=True, type=_path)
    compare.add_argument("--actual", required=True, type=_path)
    compare.add_argument("--zone", required=True)
    compare.add_argument("--output", type=_path, help="Aligned hourly output CSV")

    compare_generation_parser = subparsers.add_parser(
        "compare-generation",
        help="Compare solved daily generation with a company production reference",
    )
    compare_generation_parser.add_argument("--solved", required=True, type=_path)
    compare_generation_parser.add_argument("--reference", required=True, type=_path)
    compare_generation_parser.add_argument("--zone", default="DE00")
    compare_generation_parser.add_argument(
        "--output", type=_path, help="Aligned daily output CSV"
    )

    extract = subparsers.add_parser(
        "extract-capacities",
        help="Convert a company monthly capacity export to remake overrides",
    )
    extract.add_argument("--source", required=True, type=_path)
    extract.add_argument("--year", type=_year, default=2030)
    extract.add_argument("--bus", default="DE00")
    extract.add_argument(
        "--base-capacities",
        type=_path,
        default=DEFAULT_BASE_CAPACITIES,
        help="Base PEMMDB grouped-capacity table used to derive model splits",
    )
    extract.add_argument(
        "--output-dir",
        type=_path,
        default=DEFAULT_COMPANY_OUTPUT_DIR,
    )

    availability = subparsers.add_parser(
        "extract-availability",
        help="Convert a company daily supply forecast to operational overrides",
    )
    availability.add_argument("--source", required=True, type=_path)
    availability.add_argument("--year", type=_year, default=2030)
    availability.add_argument("--climate-year", type=_climate_year, default=2009)
    availability.add_argument("--bus", default="DE00")
    availability.add_argument(
        "--capacity-override",
        type=_path,
        default=DEFAULT_CAPACITY_OVERRIDE,
    )
    availability.add_argument(
        "--input-dir",
        "--data-dir",
        dest="data_dir",
        type=_path,
        default=DEFAULT_DATA_DIR,
    )
    availability.add_argument(
        "--output-dir",
        type=_path,
        default=DEFAULT_COMPANY_OUTPUT_DIR,
    )
    return parser


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def _metadata_args(args: argparse.Namespace) -> dict:
    return {
        key: str(_absolute(value)) if isinstance(value, Path) else value
        for key, value in vars(args).items()
    }


def run_forecast(args: argparse.Namespace) -> int:
    output_dir = _absolute(args.output_dir)
    built_path = output_dir / "built" / f"{args.tag}.nc"
    solved_path = output_dir / "solved" / f"{args.tag}.nc"
    metadata_path = output_dir / "runs" / f"{args.tag}.json"
    metadata = {
        "tag": args.tag,
        "started_at": utc_now(),
        "status": "loading",
        "cli_args": _metadata_args(args),
        "git": git_provenance(ROOT),
        "outputs": {
            "built_network": str(built_path),
            "solved_network": str(solved_path) if args.solve else None,
            "metadata": str(metadata_path),
        },
        "solve_result": None,
        "error": None,
    }
    write_json(metadata_path, metadata)

    try:
        data = load_remake_data(
            data_dir=_absolute(args.data_dir),
            climate_year=args.climate_year,
            gas_price=args.gas_price,
            coal_price=args.coal_price,
            co2_price=args.co2_price,
            capacity_override=args.capacity_override,
            technology_override=args.technology_override,
            nuclear_profile_override=args.nuclear_profile_override,
            demand_override=args.demand_override,
            vre_override=args.vre_override,
        )
        buses = set(data["buses"]["bus_id"].astype(str))
        links = set(data["links"].index.astype(str))
        battery_buses = set(
            data["capacities"].loc[
                data["capacities"]["pypsa_carrier"].astype(str).str.contains("battery"),
                "bus",
            ].astype(str)
        )
        battery = (
            read_battery_override(args.battery_override, buses, battery_buses)
            if args.battery_override
            else None
        )
        ntc = read_ntc_override(args.ntc_override, links) if args.ntc_override else None
        generator_availability = (
            read_generator_availability_override(
                args.generator_availability_override,
                data["capacities"],
            )
            if args.generator_availability_override
            else None
        )
        metadata["status"] = "building"
        write_json(metadata_path, metadata)
        build_single_network(
            data,
            BuildConfig(
                built_network_path=built_path,
                ntc_scale=args.ntc_scale,
                load_scale=args.load_scale,
                battery_scale=args.battery_scale,
                battery_override_df=battery,
                ntc_override_df=ntc,
                generator_availability_df=generator_availability,
                battery_extendable=args.battery_extendable,
                slack_cost=args.slack_cost,
            ),
        )
        metadata["status"] = "built"

        if args.solve:
            from scenarios.solve_scenarios import solve_network

            metadata["status"] = "solving"
            write_json(metadata_path, metadata)
            result = solve_network(built_path, solved_path, threads=args.threads)
            metadata["solve_result"] = result
            metadata["status"] = "solved" if result.get("status") == "solved" else "solve_failed"
            if metadata["status"] != "solved":
                raise RuntimeError(
                    "Solver did not finish successfully: "
                    f"{result.get('termination_condition', result.get('status'))}"
                )
        return 0
    except Exception as exc:
        metadata["status"] = "failed"
        metadata["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        metadata["finished_at"] = utc_now()
        write_json(metadata_path, metadata)


def _read_actual_prices(path: Path, zone: str) -> pd.Series:
    if not path.is_file():
        raise OverrideValidationError(f"Actual-prices file does not exist: {path}")
    actual = read_table(path, "actual-prices")
    if actual.empty:
        raise OverrideValidationError("Actual-prices file is empty")
    timestamp = next(
        (name for name in ("snapshot", "timestamp", "datetime", "time") if name in actual),
        actual.columns[0],
    )
    index = pd.to_datetime(actual[timestamp], errors="coerce")
    if index.isna().any():
        raise OverrideValidationError("Actual-prices file contains invalid timestamps")

    if zone in actual.columns:
        values = actual[zone]
    else:
        zone_column = next((name for name in ("zone", "bus", "bidding_zone") if name in actual), None)
        price_column = next(
            (
                name
                for name in ("price_eur_mwh", "actual_price", "price", "value")
                if name in actual
            ),
            None,
        )
        if price_column is not None and zone_column is None:
            values = actual[price_column]
        elif zone_column is None or price_column is None:
            raise OverrideValidationError(
                f"Actual-prices file needs a '{zone}' column or long-form zone and price columns"
            )
        else:
            selected = actual[zone_column].astype(str).eq(zone)
            index = index[selected]
            values = actual.loc[selected, price_column]

    numeric = pd.to_numeric(values, errors="coerce")
    series = pd.Series(numeric.to_numpy(), index=index, name="actual_price_eur_mwh")
    if series.index.duplicated().any():
        raise OverrideValidationError("Actual-prices file contains duplicate timestamps")
    if series.isna().any():
        raise OverrideValidationError("Actual prices contain missing or non-numeric values")
    return series.sort_index()


def calculate_metrics(aligned: pd.DataFrame) -> dict[str, float | int | None]:
    error = aligned["model_price_eur_mwh"] - aligned["actual_price_eur_mwh"]
    nonzero = aligned["actual_price_eur_mwh"].ne(0)
    mape = float((error[nonzero].abs() / aligned.loc[nonzero, "actual_price_eur_mwh"].abs()).mean() * 100) if nonzero.any() else None
    correlation = aligned["model_price_eur_mwh"].corr(aligned["actual_price_eur_mwh"])
    return {
        "observations": int(len(aligned)),
        "mae_eur_mwh": float(error.abs().mean()),
        "rmse_eur_mwh": float(np.sqrt(np.mean(np.square(error)))),
        "mape_percent": mape,
        "mean_bias_eur_mwh": float(error.mean()),
        "correlation": None if pd.isna(correlation) else float(correlation),
    }


def compare_prices(args: argparse.Namespace) -> int:
    import pypsa

    solved = _absolute(args.solved)
    if not solved.is_file():
        raise OverrideValidationError(f"Solved network does not exist: {solved}")
    network = pypsa.Network(str(solved))
    prices = network.buses_t.marginal_price
    if args.zone not in prices.columns:
        raise OverrideValidationError(f"Zone {args.zone!r} is not present in the solved network")
    model = pd.to_numeric(prices[args.zone], errors="coerce").rename("model_price_eur_mwh")
    actual = _read_actual_prices(_absolute(args.actual), args.zone)
    aligned = pd.concat([model, actual], axis=1, join="inner").dropna()
    if aligned.empty:
        raise OverrideValidationError("Modeled and actual prices have no overlapping timestamps")

    output = _absolute(args.output) if args.output else solved.with_name(
        f"{solved.stem}_comparison_{args.zone}.csv"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    aligned.to_csv(output, index_label="snapshot")
    result = {"zone": args.zone, "output": str(output), **calculate_metrics(aligned)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def compare_generation_cli(args: argparse.Namespace) -> int:
    import pypsa

    solved = _absolute(args.solved)
    if not solved.is_file():
        raise OverrideValidationError(f"Solved network does not exist: {solved}")
    reference_path = _absolute(args.reference)
    reference = read_production_reference(reference_path, args.zone)
    network = pypsa.Network(str(solved))
    aligned, report = compare_generation(network, reference, args.zone)
    output = _absolute(args.output) if args.output else solved.with_name(
        f"{solved.stem}_generation_comparison_{args.zone}.csv"
    )
    metrics_path = output.with_suffix(".metrics.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    aligned.to_csv(output, index=False, float_format="%.8f", date_format="%Y-%m-%d")
    payload = {
        "solved": str(solved),
        "reference": str(reference_path),
        "output": str(output),
        "metrics": str(metrics_path),
        **report,
    }
    write_json(metrics_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def extract_capacities(args: argparse.Namespace) -> int:
    result = extract_company_capacities(
        source_path=_absolute(args.source),
        base_capacities=_absolute(args.base_capacities),
        output_dir=_absolute(args.output_dir),
        bus=args.bus,
        year=args.year,
    )
    print(
        json.dumps(
            {
                "status": "extracted",
                "capacity_override": str(result.capacity_path),
                "battery_override": str(result.battery_path),
                "audit": str(result.audit_path),
                "capacity_rows": int(len(result.capacity_override)),
                "battery_rows": int(len(result.battery_override)),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def extract_availability(args: argparse.Namespace) -> int:
    result = extract_company_availability(
        source_path=_absolute(args.source),
        capacity_override=_absolute(args.capacity_override),
        data_dir=_absolute(args.data_dir),
        output_dir=_absolute(args.output_dir),
        bus=args.bus,
        year=args.year,
        climate_year=args.climate_year,
    )
    print(
        json.dumps(
            {
                "status": "extracted",
                "vre_override": str(result.vre_path),
                "generator_availability_override": str(result.generator_path),
                "production_reference": str(result.production_path),
                "audit": str(result.audit_path),
                "vre_rows": int(len(result.vre_override)),
                "generator_rows": int(len(result.generator_override)),
                "production_rows": int(len(result.production_reference)),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _normalise_argv(argv: list[str]) -> list[str]:
    if argv and argv[0] not in {
        "run",
        "compare",
        "compare-generation",
        "extract-capacities",
        "extract-availability",
        "-h",
        "--help",
    }:
        return ["run", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(_normalise_argv(list(sys.argv[1:] if argv is None else argv)))
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "compare":
            return compare_prices(args)
        if args.command == "compare-generation":
            return compare_generation_cli(args)
        if args.command == "extract-capacities":
            return extract_capacities(args)
        if args.command == "extract-availability":
            return extract_availability(args)
        return run_forecast(args)
    except (
        OverrideValidationError,
        FileNotFoundError,
        ImportError,
        RuntimeError,
        ValueError,
    ) as exc:
        parser.exit(1, f"error: {exc}\n")
