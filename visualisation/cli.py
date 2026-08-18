"""Command-line interface for the solved-network dashboard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .visualisation import DashboardError, generate_dashboard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a standalone HTML dashboard from a solved PyPSA .nc file."
    )
    parser.add_argument("input_nc", type=Path, help="Solved PyPSA NetCDF file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output HTML path (default: visualisation/output/<input>_dashboard.html)",
    )
    parser.add_argument(
        "--default-zone",
        help="Initially selected bidding zone (default: DE00 if present)",
    )
    parser.add_argument("--title", help="Optional dashboard title")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = generate_dashboard(
            args.input_nc,
            output_path=args.output,
            default_zone=args.default_zone,
            title=args.title,
        )
    except (DashboardError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Dashboard written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
