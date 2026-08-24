from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from remake.errors import OverrideValidationError
from remake.input_data import load_hourly_input, resolve_table


class RemakeInputDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.profile = pd.DataFrame(
            {"DE00": [0.5] * 8760},
            index=pd.date_range("2009-01-01", periods=8760, freq="h"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_hourly_csv_is_loaded_without_parquet_and_normalised_to_2030(self) -> None:
        self.profile.to_csv(
            self.directory / "pecd_data_Wind_Onshore_2030.csv"
        )
        with patch("pandas.read_parquet", side_effect=AssertionError("Parquet read")):
            result = load_hourly_input(self.directory, "wind_onshore", 2009)

        self.assertEqual(result.shape, (8760, 1))
        self.assertEqual(result.index[0], pd.Timestamp("2030-01-01"))
        self.assertEqual(result["DE00"].iloc[-1], 0.5)

    def test_hourly_input_rejects_wrong_climate_year(self) -> None:
        self.profile.to_csv(
            self.directory / "pecd_data_Wind_Onshore_2030.csv"
        )
        with self.assertRaisesRegex(OverrideValidationError, "not requested climate year"):
            load_hourly_input(self.directory, "wind_onshore", 2010)

    def test_duplicate_csv_and_excel_base_inputs_are_ambiguous(self) -> None:
        (self.directory / "buses.csv").touch()
        (self.directory / "buses.xlsx").touch()
        with self.assertRaisesRegex(OverrideValidationError, "Ambiguous bus base"):
            resolve_table(self.directory, "buses.csv", "bus base")


if __name__ == "__main__":
    unittest.main()
