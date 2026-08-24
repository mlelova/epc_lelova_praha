from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scenarios.load_network_data_additional import (
    _load_pecd_profiles,
    _read_pecd_parquet,
)


class PecdParquetLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.tyndp_dir = Path(self.temporary.name)
        self.preprocessed = self.tyndp_dir / "preprocessed"
        self.preprocessed.mkdir()
        self.index = pd.MultiIndex.from_product(
            [[2009], range(8760)], names=["climate_year", "hour"]
        )
        self.offshore_cap = pd.DataFrame(
            {
                "bus": ["DE00", "GB00"],
                "total_existing_mw": [300.0, 50.0],
                "zones": [
                    "DEOH001 (100 MW); DEOH002 (200 MW)",
                    "GBOH003 (50 MW)",
                ],
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, filename: str, values: dict[str, float]) -> None:
        pd.DataFrame(
            {column: [value] * 8760 for column, value in values.items()},
            index=self.index,
        ).to_parquet(self.preprocessed / filename)

    def write_bundle(self) -> None:
        self.write("pecd_wind_onshore.parquet", {"DE00": 0.2, "UK00": 0.3})
        self.write(
            "pecd_wind_offshore.parquet",
            {"DEOH001": 0.2, "DEOH002": 0.6, "UKOH003": 0.4},
        )
        self.write(
            "pecd_solar_generic.parquet",
            {"DE00": 0.1, "UK00": 0.12, "ITCA": 0.13},
        )
        self.write("pecd_solar_utility.parquet", {"ITCA": 0.3})
        self.write("pecd_solar_rooftop.parquet", {"ITCA": 0.25})

    def test_complete_bundle_selects_year_and_preserves_profile_rules(self) -> None:
        self.write_bundle()

        profiles, source = _load_pecd_profiles(
            self.tyndp_dir, 2009, self.offshore_cap
        )

        self.assertEqual(source, "preprocessed parquet")
        self.assertEqual(len(profiles["wind_onshore"]), 8760)
        self.assertEqual(profiles["wind_onshore"].index[0], pd.Timestamp("2030-01-01"))
        self.assertEqual(profiles["wind_onshore"]["GB00"].iloc[0], 0.3)
        self.assertAlmostEqual(profiles["wind_offshore"]["DE00"].iloc[0], 7 / 15)
        self.assertEqual(profiles["wind_offshore"]["GB00"].iloc[0], 0.4)
        self.assertEqual(profiles["solar_utility"]["DE00"].iloc[0], 0.1)
        self.assertEqual(profiles["solar_utility"]["ITCA"].iloc[0], 0.3)
        self.assertEqual(profiles["solar_rooftop"]["ITCA"].iloc[0], 0.25)
        self.assertEqual(profiles["solar_rooftop"]["GB00"].iloc[0], 0.12)

    def test_partial_bundle_is_rejected(self) -> None:
        self.write("pecd_wind_onshore.parquet", {"DE00": 0.2})

        with self.assertRaisesRegex(FileNotFoundError, "Incomplete.*missing"):
            _load_pecd_profiles(self.tyndp_dir, 2009, self.offshore_cap)

    def test_missing_climate_year_is_rejected(self) -> None:
        self.write("pecd_wind_onshore.parquet", {"DE00": 0.2})

        with self.assertRaisesRegex(KeyError, "Climate year 2010 not found"):
            _read_pecd_parquet(
                self.preprocessed / "pecd_wind_onshore.parquet", 2010
            )

    def test_malformed_hours_and_invalid_values_are_rejected(self) -> None:
        malformed = self.preprocessed / "malformed.parquet"
        pd.DataFrame(
            {"DE00": [0.2] * 8759},
            index=pd.MultiIndex.from_product(
                [[2009], range(8759)], names=["climate_year", "hour"]
            ),
        ).to_parquet(malformed)
        with self.assertRaisesRegex(ValueError, "hours 0..8759"):
            _read_pecd_parquet(malformed, 2009)

        invalid = self.preprocessed / "invalid.parquet"
        values = [0.2] * 8760
        values[10] = 1.1
        pd.DataFrame({"DE00": values}, index=self.index).to_parquet(invalid)
        with self.assertRaisesRegex(ValueError, r"within \[0, 1\]"):
            _read_pecd_parquet(invalid, 2009)

    def test_no_parquets_falls_back_to_raw_csv_loaders(self) -> None:
        frame = pd.DataFrame(
            {"DE00": [0.5] * 8760},
            index=pd.date_range("2030-01-01", periods=8760, freq="h"),
        )
        with (
            patch(
                "scenarios.load_network_data_additional._load_pecd_onshore",
                return_value=frame,
            ) as onshore,
            patch(
                "scenarios.load_network_data_additional._load_pecd_offshore",
                return_value=frame,
            ) as offshore,
            patch(
                "scenarios.load_network_data_additional._load_pecd_solar",
                side_effect=[frame, frame],
            ) as solar,
        ):
            profiles, source = _load_pecd_profiles(
                self.tyndp_dir, 2009, self.offshore_cap
            )

        self.assertEqual(source, "raw CSV")
        self.assertEqual(set(profiles), {
            "wind_onshore",
            "wind_offshore",
            "solar_utility",
            "solar_rooftop",
        })
        onshore.assert_called_once_with(self.tyndp_dir / "PECD 2030", 2009)
        offshore.assert_called_once()
        self.assertEqual(solar.call_count, 2)


if __name__ == "__main__":
    unittest.main()
