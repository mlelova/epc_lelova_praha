from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal

from scenarios.load_network_data_additional import _apply_gas_co2_prices


class FuelPriceAdjustmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.technologies = pd.DataFrame(
            [
                {
                    "index_carrier": "coal",
                    "pypsa_carrier": "coal",
                    "fuel_type": "coal",
                    "efficiency": 0.4,
                    "vom_eur_mwh": 2.0,
                    "fuel_price_eur_mwh": 6.0,
                    "co2_tco2_mwh": 0.3,
                    "marginal_cost_eur_mwh": 101.0,
                },
                {
                    "index_carrier": "coal-ccs",
                    "pypsa_carrier": "coal-ccs",
                    "fuel_type": "coal",
                    "efficiency": 0.35,
                    "vom_eur_mwh": 3.0,
                    "fuel_price_eur_mwh": 6.0,
                    "co2_tco2_mwh": 0.03,
                    "marginal_cost_eur_mwh": 102.0,
                },
                {
                    "index_carrier": "chp-hard-coal",
                    "pypsa_carrier": "other-thermal",
                    "fuel_type": "Coal",
                    "efficiency": 0.3,
                    "vom_eur_mwh": 4.0,
                    "fuel_price_eur_mwh": 6.0,
                    "co2_tco2_mwh": 0.32,
                    "marginal_cost_eur_mwh": 103.0,
                },
                {
                    "index_carrier": "gas",
                    "pypsa_carrier": "gas",
                    "fuel_type": "gas",
                    "efficiency": 0.5,
                    "vom_eur_mwh": 1.0,
                    "fuel_price_eur_mwh": 20.0,
                    "co2_tco2_mwh": 0.2,
                    "marginal_cost_eur_mwh": 104.0,
                },
                {
                    "index_carrier": "lignite",
                    "pypsa_carrier": "lignite",
                    "fuel_type": "lignite",
                    "efficiency": 0.4,
                    "vom_eur_mwh": 2.5,
                    "fuel_price_eur_mwh": 5.0,
                    "co2_tco2_mwh": 0.4,
                    "marginal_cost_eur_mwh": 105.0,
                },
            ]
        ).set_index("index_carrier")

    def test_coal_override_updates_direct_ccs_and_chp_rows(self) -> None:
        result = _apply_gas_co2_prices(
            self.technologies,
            gas_price=None,
            co2_price=100.0,
            coal_price=10.0,
        )

        for carrier in ("coal", "coal-ccs", "chp-hard-coal"):
            row = self.technologies.loc[carrier]
            expected = (
                row["vom_eur_mwh"]
                + 10.0 / row["efficiency"]
                + row["co2_tco2_mwh"] / row["efficiency"] * 100.0
            )
            self.assertAlmostEqual(
                result.loc[carrier, "marginal_cost_eur_mwh"], expected
            )

        self.assertEqual(result.loc["gas", "marginal_cost_eur_mwh"], 104.0)
        self.assertAlmostEqual(
            result.loc["lignite", "marginal_cost_eur_mwh"],
            2.5 + 5.0 / 0.4 + 0.4 / 0.4 * 100.0,
        )

    def test_coal_override_updates_all_production_coal_rows(self) -> None:
        data_path = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "open-tyndp"
            / "technologies_2030.csv"
        )
        technologies = pd.read_csv(data_path)
        coal_mask = technologies["fuel_type"].astype(str).str.lower().eq("coal")

        result = _apply_gas_co2_prices(
            technologies,
            gas_price=None,
            co2_price=85.0,
            coal_price=10.0,
        )
        expected = (
            technologies.loc[coal_mask, "vom_eur_mwh"].fillna(0.0)
            + 10.0 / technologies.loc[coal_mask, "efficiency"]
            + technologies.loc[coal_mask, "co2_tco2_mwh"]
            / technologies.loc[coal_mask, "efficiency"]
            * 85.0
        )

        self.assertEqual(int(coal_mask.sum()), 7)
        pd.testing.assert_series_equal(
            result.loc[coal_mask, "marginal_cost_eur_mwh"],
            expected,
            check_names=False,
        )

    def test_coal_override_does_not_change_other_fuels_without_co2_price(self) -> None:
        result = _apply_gas_co2_prices(
            self.technologies,
            gas_price=None,
            co2_price=None,
            coal_price=9.0,
        )

        self.assertEqual(result.loc["gas", "marginal_cost_eur_mwh"], 104.0)
        self.assertEqual(result.loc["lignite", "marginal_cost_eur_mwh"], 105.0)

    def test_coal_override_without_co2_price_uses_zero_carbon_term(self) -> None:
        result = _apply_gas_co2_prices(
            self.technologies,
            gas_price=None,
            co2_price=None,
            coal_price=8.0,
        )

        self.assertAlmostEqual(
            result.loc["coal", "marginal_cost_eur_mwh"], 2.0 + 8.0 / 0.4
        )

    def test_no_coal_override_retains_csv_fuel_price_behavior(self) -> None:
        result = _apply_gas_co2_prices(
            self.technologies,
            gas_price=None,
            co2_price=50.0,
            coal_price=None,
        )

        self.assertAlmostEqual(
            result.loc["coal", "marginal_cost_eur_mwh"],
            2.0 + 6.0 / 0.4 + 0.3 / 0.4 * 50.0,
        )

    def test_input_dataframe_is_not_mutated(self) -> None:
        original = self.technologies.copy(deep=True)

        _apply_gas_co2_prices(
            self.technologies,
            gas_price=25.0,
            co2_price=90.0,
            coal_price=10.0,
        )

        assert_frame_equal(self.technologies, original)


if __name__ == "__main__":
    unittest.main()
