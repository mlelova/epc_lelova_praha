from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pypsa

from visualisation import DashboardError, generate_dashboard
from visualisation.visualisation import _build_dashboard_data


def build_solved_fixture() -> pypsa.Network:
    snapshots = pd.date_range("2030-01-01", periods=48, freq="h")
    hours = np.tile(np.arange(24, dtype=float), 2)
    network = pypsa.Network()
    network.set_snapshots(snapshots)
    network.snapshot_weightings.loc[:, :] = 1.0

    network.add("Carrier", "solar", nice_name="Solar", color="#ffb020")
    network.add("Carrier", "gas", nice_name="Gas", color="#e0567a")
    network.add("Carrier", "battery", nice_name="Battery", color="#9ad36a")
    for bus in ("A", "B", "C"):
        network.add("Bus", bus, country=bus)

    network.add("Load", "A-load", bus="A")
    network.loads_t.p_set.loc[:, "A-load"] = 10.0 + hours

    network.add("Generator", "A-solar", bus="A", carrier="solar", p_nom=5.0)
    network.add("Generator", "A-gas", bus="A", carrier="gas", p_nom=40.0)
    network.add("Generator", "B-gas", bus="B", carrier="gas", p_nom=10.0)
    network.generators.loc[:, "p_nom_opt"] = network.generators.p_nom
    network.generators_t.p = pd.DataFrame(
        {
            "A-solar": np.full(48, 2.0),
            "A-gas": 8.0 + hours,
            "B-gas": np.full(48, 1.0),
        },
        index=snapshots,
    )

    network.add(
        "StorageUnit",
        "A-battery",
        bus="A",
        carrier="battery",
        p_nom=10.0,
        max_hours=2.0,
        efficiency_store=0.9,
        efficiency_dispatch=0.8,
    )
    network.storage_units.loc[:, "p_nom_opt"] = network.storage_units.p_nom
    battery_dispatch = np.zeros(48)
    for start in (0, 24):
        battery_dispatch[start : start + 6] = -5.0
        battery_dispatch[start + 18 : start + 24] = 5.0
    network.storage_units_t.p = pd.DataFrame(
        {"A-battery": battery_dispatch}, index=snapshots
    )
    network.storage_units_t.state_of_charge = pd.DataFrame(
        {"A-battery": np.tile(np.linspace(0.0, 20.0, 24), 2)}, index=snapshots
    )

    network.add(
        "Link",
        "A-B",
        bus0="A",
        bus1="B",
        carrier="DC",
        p_nom=500.0,
        efficiency=0.9,
    )
    network.links_t.p0 = pd.DataFrame({"A-B": np.full(48, 200.0)}, index=snapshots)
    network.links_t.p1 = pd.DataFrame({"A-B": np.full(48, -180.0)}, index=snapshots)

    network.buses_t.marginal_price = pd.DataFrame(
        {"A": hours, "B": hours + 5.0, "C": np.full(48, 30.0)}, index=snapshots
    )
    network.name = "Deterministic fixture"
    return network


class DashboardDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.network = build_solved_fixture()
        self.data = _build_dashboard_data(
            self.network, Path("fixture.nc"), default_zone="A"
        )

    def test_price_demand_and_tb_metrics(self) -> None:
        zone = self.data["zones"]["A"]
        self.assertEqual(zone["price"]["mean"], 11.5)
        self.assertEqual(zone["price"]["tb"]["tb1"]["spread_eur_mwh"], 23.0)
        self.assertEqual(zone["price"]["tb"]["tb2"]["spread_eur_mwh"], 22.0)
        self.assertEqual(zone["price"]["tb"]["tb4"]["spread_eur_mwh"], 20.0)
        self.assertAlmostEqual(zone["demand"]["annual_twh"], 0.001032, places=6)
        regression = zone["residual_load"]["regression"]
        self.assertAlmostEqual(regression["correlation"], 1.0, places=3)
        self.assertAlmostEqual(regression["slope_eur_mwh_per_gw"], 1000.0, places=3)

    def test_battery_energy_revenue_and_cycles(self) -> None:
        battery = self.data["zones"]["A"]["battery"]
        self.assertEqual(battery["power_gw"], 0.01)
        self.assertEqual(battery["energy_gwh"], 0.02)
        self.assertEqual(battery["charge_twh"], 0.00006)
        self.assertEqual(battery["discharge_twh"], 0.00006)
        self.assertEqual(battery["equivalent_cycles"], 3.0)
        self.assertEqual(battery["weighted_charge_price"], 2.5)
        self.assertEqual(battery["weighted_discharge_price"], 20.5)
        self.assertEqual(battery["gross_revenue_meur"], 0.001)

    def test_link_terminal_direction_and_missing_components(self) -> None:
        zone_a = self.data["zones"]["A"]
        zone_b = self.data["zones"]["B"]
        zone_c = self.data["zones"]["C"]
        self.assertEqual(zone_a["flows"]["exports_twh"], 0.0096)
        self.assertEqual(zone_a["flows"]["net_import_twh"], -0.0096)
        self.assertEqual(zone_b["flows"]["imports_twh"], 0.00864)
        self.assertEqual(zone_b["flows"]["net_import_twh"], 0.00864)
        self.assertIsNone(zone_c["battery"])
        self.assertFalse(zone_c["has_load"])
        self.assertIsNone(zone_c["demand"]["annual_twh"])

    def test_unknown_default_zone_is_rejected(self) -> None:
        with self.assertRaisesRegex(DashboardError, "Unknown default zone"):
            _build_dashboard_data(
                self.network, Path("fixture.nc"), default_zone="missing"
            )

    def test_unsolved_network_is_rejected(self) -> None:
        unsolved = build_solved_fixture()
        unsolved.buses_t.marginal_price = pd.DataFrame(index=unsolved.snapshots)
        with self.assertRaisesRegex(DashboardError, "No bus marginal prices"):
            _build_dashboard_data(unsolved, Path("unsolved.nc"))

    def test_non_datetime_snapshots_are_rejected(self) -> None:
        incompatible = pypsa.Network()
        incompatible.set_snapshots(pd.Index([0, 1], name="snapshot"))
        with self.assertRaisesRegex(DashboardError, "DatetimeIndex"):
            _build_dashboard_data(incompatible, Path("incompatible.nc"))

    def test_snapshot_weightings_scale_energy_and_revenue(self) -> None:
        weighted = build_solved_fixture()
        weighted.snapshot_weightings.loc[
            :, ["objective", "stores", "generators"]
        ] = 2.0
        data = _build_dashboard_data(weighted, Path("weighted.nc"), default_zone="A")
        zone = data["zones"]["A"]
        self.assertEqual(zone["demand"]["annual_twh"], 0.002064)
        self.assertEqual(zone["battery"]["discharge_twh"], 0.00012)
        self.assertEqual(zone["battery"]["equivalent_cycles"], 6.0)
        self.assertEqual(zone["flows"]["exports_twh"], 0.0192)


class DashboardGenerationTests(unittest.TestCase):
    def test_standalone_html_generation(self) -> None:
        output_dir = Path("visualisation/output").resolve()
        source = output_dir / f"_test_fixture_{os.getpid()}.nc"
        destination = output_dir / f"_test_fixture_{os.getpid()}.html"
        try:
            source.write_bytes(b"test fixture")
            fixture = build_solved_fixture()
            with patch.object(fixture, "import_from_netcdf", return_value=None), patch(
                "visualisation.visualisation.pypsa.Network", return_value=fixture
            ):
                result = generate_dashboard(
                    source,
                    output_path=destination,
                    default_zone="A",
                    title="<Fixture dashboard>",
                )
            self.assertEqual(result, destination.resolve())
            page = destination.read_text(encoding="utf-8")
            self.assertIn("&lt;Fixture dashboard&gt;", page)
            self.assertNotRegex(page, r"https?://")
            match = re.search(
                r'<script type="application/json" id="dashboard-data">(.*?)</script>',
                page,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(match)
            payload = json.loads(match.group(1))
            self.assertEqual(payload["default_zone"], "A")
            self.assertEqual(set(payload["zones"]), {"A", "B", "C"})
        finally:
            source.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)

    def test_missing_input_is_rejected(self) -> None:
        with self.assertRaises(FileNotFoundError):
            generate_dashboard("definitely_missing_network.nc")


if __name__ == "__main__":
    unittest.main()
