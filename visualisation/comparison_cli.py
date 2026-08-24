"""Command-line interface for comparing two solved-network dashboards."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .comparison import generate_comparison_dashboard
from .visualisation import DashboardError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an offline comparison dashboard from two solved PyPSA networks."
    )
    parser.add_argument("current_nc", type=Path, help="Current solved PyPSA NetCDF file")
    parser.add_argument("baseline_nc", type=Path, help="Baseline solved PyPSA NetCDF file")
    parser.add_argument("-o", "--output", type=Path, help="Output HTML path")
    parser.add_argument(
        "--default-zone",
        help="Initially selected scope, including EUROPE (default: DE00 if present)",
    )
    parser.add_argument("--title", help="Optional dashboard title")
    parser.add_argument(
        "--current-label", default="Latest calibration", help="Current model label"
    )
    parser.add_argument(
        "--baseline-label", default="Old baseline", help="Baseline model label"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = generate_comparison_dashboard(
            args.current_nc,
            args.baseline_nc,
            output_path=args.output,
            default_zone=args.default_zone,
            title=args.title,
            current_label=args.current_label,
            baseline_label=args.baseline_label,
        )
    except (DashboardError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Comparison dashboard written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
