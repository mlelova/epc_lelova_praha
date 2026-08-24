from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tests.test_visualisation import build_solved_fixture
from visualisation import DashboardError, generate_comparison_dashboard
from visualisation.comparison import _build_comparison_data


def build_comparison_fixture():
    baseline = build_solved_fixture()
    current = build_solved_fixture()
    current.buses_t.marginal_price.loc[:, "A"] += 10.0
    current.add("Carrier", "new-tech", nice_name="New Tech", color="#445566")
    current.add("Generator", "A-new-tech", bus="A", carrier="new-tech", p_nom=4.0)
    current.generators.loc["A-new-tech", "p_nom_opt"] = 4.0
    current.generators_t.p.loc[:, "A-new-tech"] = 1.0
    current.storage_units.loc["A-battery", ["p_nom", "p_nom_opt"]] = 20.0
    current.links_t.p0.loc[:, "A-B"] = 250.0
    current.links_t.p1.loc[:, "A-B"] = -225.0
    current._objective = 1200.0
    baseline._objective = 1000.0
    return current, baseline


class ComparisonDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current, self.baseline = build_comparison_fixture()
        self.data = _build_comparison_data(
            self.current,
            self.baseline,
            Path("current.nc"),
            Path("baseline.nc"),
            default_zone="A",
        )

    def test_zonal_and_aggregate_deltas(self) -> None:
        zone = self.data["comparison"]["zones"]["A"]
        europe = self.data["comparison"]["zones"]["EUROPE"]
        self.assertEqual(zone["summary"]["mean_price_eur_mwh"]["delta"], 10.0)
        self.assertEqual(europe["summary"]["mean_price_eur_mwh"]["delta"], 10.0)
        self.assertEqual(zone["summary"]["battery_power_gw"]["delta"], 0.01)
        self.assertEqual(
            zone["summary"]["battery_power_gw"]["pct_delta"], 100.0
        )
        self.assertEqual(
            self.data["comparison"]["system_objective_eur"]["pct_delta"], 20.0
        )
        self.assertGreater(zone["summary"]["exports_twh"]["delta"], 0.0)
        self.assertGreater(
            europe["summary"]["internal_transfer_twh"]["delta"], 0.0
        )

    def test_union_carrier_and_zero_baseline_percentage(self) -> None:
        carriers = {
            row["carrier"]: row
            for row in self.data["comparison"]["zones"]["A"]["carriers"]
        }
        added = carriers["new-tech"]
        self.assertEqual(added["capacity_gw"]["current"], 0.004)
        self.assertEqual(added["capacity_gw"]["baseline"], 0.0)
        self.assertEqual(added["capacity_gw"]["delta"], 0.004)
        self.assertIsNone(added["capacity_gw"]["pct_delta"])

    def test_scope_without_battery_or_load_remains_comparable(self) -> None:
        zone = self.data["comparison"]["zones"]["C"]["summary"]
        self.assertEqual(zone["battery_power_gw"]["current"], 0.0)
        self.assertEqual(zone["battery_power_gw"]["baseline"], 0.0)
        self.assertIsNone(zone["battery_power_gw"]["pct_delta"])
        self.assertIsNone(zone["demand_twh"]["current"])
        self.assertIsNone(zone["demand_twh"]["delta"])

    def test_mismatched_snapshots_are_rejected(self) -> None:
        shifted = build_solved_fixture()
        shifted.set_snapshots(
            pd.date_range("2030-01-02", periods=48, freq="h")
        )
        with self.assertRaisesRegex(DashboardError, "identical snapshots"):
            _build_comparison_data(
                self.current,
                shifted,
                Path("current.nc"),
                Path("shifted.nc"),
            )

    def test_mismatched_zones_are_rejected(self) -> None:
        expanded = build_solved_fixture()
        expanded.add("Bus", "D", country="D")
        expanded.buses_t.marginal_price.loc[:, "D"] = 0.0
        with self.assertRaisesRegex(DashboardError, "identical zones"):
            _build_comparison_data(
                self.current,
                expanded,
                Path("current.nc"),
                Path("expanded.nc"),
            )

    def test_unsolved_input_is_rejected(self) -> None:
        unsolved = build_solved_fixture()
        unsolved.buses_t.marginal_price = pd.DataFrame(index=unsolved.snapshots)
        with self.assertRaisesRegex(DashboardError, "No bus marginal prices"):
            _build_comparison_data(
                unsolved,
                self.baseline,
                Path("unsolved.nc"),
                Path("baseline.nc"),
            )


class ComparisonGenerationTests(unittest.TestCase):
    def test_standalone_comparison_html_generation(self) -> None:
        output_dir = Path("visualisation/output").resolve()
        current_source = output_dir / f"_current_{os.getpid()}.nc"
        baseline_source = output_dir / f"_baseline_{os.getpid()}.nc"
        destination = output_dir / f"_comparison_{os.getpid()}.html"
        current, baseline = build_comparison_fixture()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            current_source.write_bytes(b"current fixture")
            baseline_source.write_bytes(b"baseline fixture")
            with patch(
                "visualisation.comparison._load_network",
                side_effect=[current, baseline],
            ):
                result = generate_comparison_dashboard(
                    current_source,
                    baseline_source,
                    output_path=destination,
                    default_zone="A",
                    title="<Comparison dashboard>",
                    current_label="Current <model>",
                    baseline_label="Baseline & model",
                )
            self.assertEqual(result, destination)
            page = destination.read_text(encoding="utf-8")
            self.assertIn("&lt;Comparison dashboard&gt;", page)
            self.assertNotRegex(page, r"https?://")
            match = re.search(
                r'<script type="application/json" id="dashboard-data">(.*?)</script>',
                page,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(match)
            payload = json.loads(match.group(1))
            self.assertEqual(payload["default_zone"], "A")
            self.assertEqual(payload["labels"]["current"], "Current <model>")
            self.assertEqual(payload["labels"]["baseline"], "Baseline & model")
            self.assertEqual(payload["models"]["current"]["network"]["source_file"], current_source.name)
            self.assertEqual(payload["models"]["baseline"]["network"]["source_file"], baseline_source.name)
        finally:
            current_source.unlink(missing_ok=True)
            baseline_source.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)

    def test_missing_input_and_invalid_output_are_rejected(self) -> None:
        with self.assertRaises(FileNotFoundError):
            generate_comparison_dashboard("missing-current.nc", "missing-baseline.nc")

        output_dir = Path("visualisation/output").resolve()
        current_source = output_dir / f"_current_{os.getpid()}.nc"
        baseline_source = output_dir / f"_baseline_{os.getpid()}.nc"
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            current_source.write_bytes(b"current fixture")
            baseline_source.write_bytes(b"baseline fixture")
            with self.assertRaisesRegex(DashboardError, "must end with .html"):
                generate_comparison_dashboard(
                    current_source,
                    baseline_source,
                    output_path=output_dir / "comparison.txt",
                )
        finally:
            current_source.unlink(missing_ok=True)
            baseline_source.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
