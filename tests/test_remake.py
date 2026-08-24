from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from remake.cli import (
    _normalise_argv,
    _read_actual_prices,
    calculate_metrics,
    make_parser,
)
from remake.load_network import (
    OverrideValidationError,
    PROFILE_KEYS,
    apply_capacity_override,
    apply_technology_override,
    _load_profile_with_override,
    read_battery_override,
    read_ntc_override,
    read_profile_override,
    load_remake_data,
    validate_remake_data,
)


class RemakeOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.data = {
            "buses": pd.DataFrame({"bus_id": ["DE00", "FR00"]}),
            "links": pd.DataFrame(
                {"p_nom": [1000.0]}, index=pd.Index(["DE00-FR00-DC"], name="link_id")
            ),
            "capacities": pd.DataFrame(
                {
                    "bus": ["DE00", "FR00"],
                    "index_carrier": ["gas-ccgt", "gas-ccgt"],
                    "p_nom": [100.0, 200.0],
                    "e_nom": [0.0, 0.0],
                }
            ),
            "technologies": pd.DataFrame(
                {
                    "index_carrier": ["gas-ccgt", "gas-ocgt"],
                    "pypsa_carrier": ["gas", "gas"],
                    "efficiency": [0.5, 0.4],
                    "vom_eur_mwh": [2.0, 3.0],
                }
            ),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def csv(self, name: str, frame: pd.DataFrame) -> Path:
        path = self.directory / name
        frame.to_csv(path, index=False)
        return path

    def xlsx(self, name: str, frame: pd.DataFrame) -> Path:
        path = self.directory / name
        frame.to_excel(path, index=False)
        return path

    def test_capacity_override_uses_explicit_mw_column(self) -> None:
        path = self.csv(
            "capacities.csv",
            pd.DataFrame(
                {"bus": ["DE00"], "index_carrier": ["gas-ccgt"], "p_nom_mw": [321.0]}
            ),
        )
        apply_capacity_override(self.data, path)
        capacities = self.data["capacities"].set_index(["bus", "index_carrier"])
        self.assertEqual(capacities.loc[("DE00", "gas-ccgt"), "p_nom"], 321.0)
        self.assertEqual(capacities.loc[("FR00", "gas-ccgt"), "p_nom"], 200.0)

    def test_capacity_override_rejects_unknown_key(self) -> None:
        path = self.csv(
            "capacities.csv",
            pd.DataFrame(
                {"bus": ["DE00"], "index_carrier": ["nuclear"], "p_nom_mw": [1.0]}
            ),
        )
        with self.assertRaisesRegex(OverrideValidationError, "Unknown capacity key"):
            apply_capacity_override(self.data, path)

    def test_capacity_override_accepts_excel(self) -> None:
        path = self.xlsx(
            "capacities.xlsx",
            pd.DataFrame(
                {"bus": ["DE00"], "index_carrier": ["gas-ccgt"], "p_nom_mw": [456.0]}
            ),
        )
        apply_capacity_override(self.data, path)
        capacities = self.data["capacities"].set_index(["bus", "index_carrier"])
        self.assertEqual(capacities.loc[("DE00", "gas-ccgt"), "p_nom"], 456.0)

    def test_technology_override_can_update_all_rows_for_pypsa_carrier(self) -> None:
        path = self.csv(
            "technologies.csv",
            pd.DataFrame({"pypsa_carrier": ["gas"], "vom_eur_mwh": [9.5]}),
        )
        apply_technology_override(self.data, path)
        self.assertEqual(self.data["technologies"]["vom_eur_mwh"].tolist(), [9.5, 9.5])

    def test_battery_and_ntc_overrides_validate_identifiers(self) -> None:
        battery_path = self.csv(
            "batteries.csv",
            pd.DataFrame({"bus": ["DE00"], "p_nom_mw": [50], "duration_h": [4]}),
        )
        ntc_path = self.csv(
            "ntc.csv",
            pd.DataFrame({"link_id": ["DE00-FR00-DC"], "p_nom": [1200]}),
        )
        battery = read_battery_override(battery_path, {"DE00", "FR00"})
        ntc = read_ntc_override(ntc_path, {"DE00-FR00-DC"})
        self.assertEqual(battery.loc[0, "duration_h"], 4)
        self.assertEqual(ntc.loc[0, "p_nom"], 1200)

    def test_profile_override_requires_8760_bounded_hourly_values(self) -> None:
        frame = pd.DataFrame(
            {
                "snapshot": pd.date_range("2030-01-01", periods=8760, freq="h"),
                "DE00": [0.8] * 8760,
            }
        )
        path = self.csv("nuclear.csv", frame)
        profile = read_profile_override(path, "nuclear override", {"DE00"})
        self.assertEqual(profile.shape, (8760, 1))

        frame.loc[10, "DE00"] = 1.1
        invalid = self.csv("invalid_nuclear.csv", frame)
        with self.assertRaisesRegex(OverrideValidationError, r"within \[0, 1\]"):
            read_profile_override(invalid, "nuclear override", {"DE00"})

    def test_partial_time_series_overrides_keep_unlisted_buses(self) -> None:
        snapshots = pd.date_range("2030-01-01", periods=8760, freq="h")
        base = {key: pd.DataFrame(0.5, index=snapshots, columns=["DE00", "FR00"])
                for key in PROFILE_KEYS}
        base.update({key: value.copy(deep=True) for key, value in self.data.items()})
        base["dsr_ts"] = pd.DataFrame(
            {"DE00_Price Band 1": [0.5] * 8760}, index=snapshots
        )
        base["dsr_static"] = pd.DataFrame()
        base["climate_year"] = 2009

        demand_path = self.csv(
            "demand.csv",
            pd.DataFrame({"snapshot": snapshots, "DE00": [100.0] * 8760}),
        )
        vre_path = self.csv(
            "vre.csv",
            pd.DataFrame(
                {
                    "snapshot": snapshots,
                    "technology": ["wind_onshore"] * 8760,
                    "bus": ["DE00"] * 8760,
                    "p_max_pu": [0.75] * 8760,
                }
            ),
        )
        fixed = {key: value for key, value in base.items() if key not in PROFILE_KEYS}
        with (
            patch("remake.load_network.load_fixed_inputs", return_value=fixed),
            patch(
                "remake.load_network.load_hourly_input",
                side_effect=lambda _directory, key, _year: base[key].copy(),
            ),
        ):
            result = load_remake_data(
                "data", 2009,
                demand_override=demand_path,
                vre_override=vre_path,
            )

        self.assertEqual(result["electricity_demand"]["DE00"].iloc[0], 100.0)
        self.assertEqual(result["electricity_demand"]["FR00"].iloc[0], 0.5)
        self.assertEqual(result["wind_onshore"]["DE00"].iloc[0], 0.75)
        self.assertEqual(result["wind_onshore"]["FR00"].iloc[0], 0.5)

    def test_complete_profile_override_skips_base_hourly_file(self) -> None:
        profile = pd.DataFrame(
            {"DE00": [0.5] * 8760},
            index=pd.date_range("2030-01-01", periods=8760, freq="h"),
        )
        with patch(
            "remake.load_network.load_hourly_input",
            side_effect=AssertionError("base profile should not be loaded"),
        ):
            result = _load_profile_with_override(
                "data", "wind_onshore", 2009, profile, {"DE00"}
            )
        self.assertIs(result, profile)


class RemakeVreValidationTests(unittest.TestCase):
    def make_data(self) -> dict:
        snapshots = pd.date_range("2030-01-01", periods=8760, freq="h")
        profiles = {
            key: pd.DataFrame(0.5, index=snapshots, columns=["DE00"])
            for key in PROFILE_KEYS
        }
        profiles.update(
            {
                "buses": pd.DataFrame({"bus_id": ["DE00", "FR00"]}),
                "links": pd.DataFrame(),
                "capacities": pd.DataFrame(
                    {
                        "bus": ["DE00"],
                        "index_carrier": ["onwind"],
                        "pypsa_carrier": ["onwind"],
                        "p_nom": [100.0],
                        "e_nom": [0.0],
                    }
                ),
                "technologies": pd.DataFrame(),
            }
        )
        return profiles

    def test_empty_vre_profile_is_rejected(self) -> None:
        data = self.make_data()
        data["wind_onshore"] = pd.DataFrame(index=data["wind_offshore"].index)

        with self.assertRaisesRegex(
            OverrideValidationError, "wind_onshore contains no bus profiles"
        ):
            validate_remake_data(data)

    def test_missing_positive_capacity_bus_profile_is_rejected(self) -> None:
        data = self.make_data()
        data["capacities"] = pd.concat(
            [
                data["capacities"],
                pd.DataFrame(
                    {
                        "bus": ["FR00"],
                        "index_carrier": ["onwind"],
                        "pypsa_carrier": ["onwind"],
                        "p_nom": [50.0],
                        "e_nom": [0.0],
                    }
                ),
            ],
            ignore_index=True,
        )

        with self.assertRaisesRegex(
            OverrideValidationError, "wind_onshore.*missing.*FR00"
        ):
            validate_remake_data(data)

    def test_all_zero_positive_capacity_bus_profile_is_rejected(self) -> None:
        data = self.make_data()
        data["wind_onshore"].loc[:, "DE00"] = 0.0

        with self.assertRaisesRegex(
            OverrideValidationError, "wind_onshore.*all-zero.*DE00"
        ):
            validate_remake_data(data)


class RemakeCliTests(unittest.TestCase):
    def test_legacy_command_shape_is_normalised_to_run(self) -> None:
        argv = _normalise_argv(["--tag", "forecast", "--build-only"])
        args = make_parser().parse_args(argv)
        self.assertEqual(args.command, "run")
        self.assertEqual(args.tag, "forecast")

    def test_extract_capacities_is_a_first_class_subcommand(self) -> None:
        argv = _normalise_argv(
            ["extract-capacities", "--source", "company_data/ins_cap.csv"]
        )
        args = make_parser().parse_args(argv)
        self.assertEqual(args.command, "extract-capacities")
        self.assertEqual(args.bus, "DE00")
        self.assertEqual(args.year, 2030)

    def test_availability_commands_are_first_class_subcommands(self) -> None:
        extraction = make_parser().parse_args(
            _normalise_argv(
                ["extract-availability", "--source", "company_data/available_cap.csv"]
            )
        )
        self.assertEqual(extraction.command, "extract-availability")
        self.assertEqual(extraction.climate_year, 2009)

        comparison = make_parser().parse_args(
            _normalise_argv(
                [
                    "compare-generation",
                    "--solved",
                    "solved.nc",
                    "--reference",
                    "production.csv",
                ]
            )
        )
        self.assertEqual(comparison.command, "compare-generation")
        self.assertEqual(comparison.zone, "DE00")

    def test_comparison_metrics(self) -> None:
        aligned = pd.DataFrame(
            {
                "model_price_eur_mwh": [10.0, 20.0, 30.0],
                "actual_price_eur_mwh": [12.0, 18.0, 30.0],
            }
        )
        metrics = calculate_metrics(aligned)
        self.assertAlmostEqual(metrics["mae_eur_mwh"], 4 / 3)
        self.assertAlmostEqual(metrics["mean_bias_eur_mwh"], 0.0)
        self.assertEqual(metrics["observations"], 3)

    def test_single_zone_actual_price_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actual.csv"
            pd.DataFrame(
                {
                    "timestamp": pd.date_range("2030-01-01", periods=2, freq="h"),
                    "price_eur_mwh": [50.0, 60.0],
                }
            ).to_csv(path, index=False)
            actual = _read_actual_prices(path, "DE00")
        self.assertEqual(actual.tolist(), [50.0, 60.0])

    def test_excel_actual_price_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actual.xlsx"
            pd.DataFrame(
                {
                    "timestamp": pd.date_range("2030-01-01", periods=2, freq="h"),
                    "DE00": [50.0, 60.0],
                }
            ).to_excel(path, index=False)
            actual = _read_actual_prices(path, "DE00")
        self.assertEqual(actual.tolist(), [50.0, 60.0])


if __name__ == "__main__":
    unittest.main()
