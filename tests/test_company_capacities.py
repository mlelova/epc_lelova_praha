from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from remake.company_capacities import (
    build_company_capacity_overrides,
    extract_company_capacities,
    hour_weighted_annual_mean,
    read_company_capacity_source,
)
from remake.load_network import (
    OverrideValidationError,
    apply_capacity_override,
    read_battery_override,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "company_data" / "ins_cap.csv"
BASE_CAPACITIES = (
    ROOT / "data" / "open-tyndp" / "pemmdb_capacities_2030_grouped.csv"
)


class CompanyCapacityExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def source_copy(self, name: str = "source.csv") -> tuple[pd.DataFrame, Path]:
        raw = pd.read_csv(
            SOURCE,
            sep=";",
            header=None,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
        return raw, self.directory / name

    @staticmethod
    def write_source(raw: pd.DataFrame, path: Path) -> Path:
        raw.to_csv(path, sep=";", header=False, index=False, lineterminator="\r\n")
        return path

    def test_source_parsing_and_hour_weighted_mean(self) -> None:
        source = read_company_capacity_source(SOURCE, 2030)
        annual, hours = hour_weighted_annual_mean(source.monthly_gw)

        self.assertEqual(source.country, "de")
        self.assertEqual(source.unit, "GW")
        self.assertEqual(len(source.monthly_gw), 12)
        self.assertEqual(int(hours.sum()), 8760)
        self.assertAlmostEqual(annual["btry"], 11.49)
        self.assertAlmostEqual(annual["spv"], 187.8890410958904)
        self.assertAlmostEqual(annual["lig"], 6.756136986336986)

    def test_full_mapping_preserves_selected_baseline_ratios(self) -> None:
        source = read_company_capacity_source(SOURCE, 2030)
        capacity, battery, audit = build_company_capacity_overrides(
            source, BASE_CAPACITIES, "DE00", 2030
        )
        mapped = capacity.set_index("index_carrier")
        base = pd.read_csv(BASE_CAPACITIES)
        base = base[base["bus"].eq("DE00")].set_index("index_carrier")

        self.assertAlmostEqual(mapped.loc["gas-ccgt", "p_nom_mw"], 19650.0)
        self.assertAlmostEqual(mapped.loc["gas-ocgt", "p_nom_mw"], 3854.9315, places=3)
        self.assertAlmostEqual(mapped.loc["other-res", "p_nom_mw"], 9370.0)
        self.assertAlmostEqual(
            mapped.loc[["solar-pv-utility", "solar-pv-rooftop"], "p_nom_mw"].sum(),
            187889.0410958904,
        )
        gas_rows = [
            carrier
            for carrier in mapped.index
            if carrier == "gas-conv" or carrier.startswith("chp-gas-conventional")
        ]
        self.assertAlmostEqual(mapped.loc[gas_rows, "p_nom_mw"].sum(), 5820.0)
        self.assertAlmostEqual(
            mapped.loc[["hydro-phs-turbine", "hydro-phs-pure-turbine"], "p_nom_mw"].sum(),
            6490.0,
        )

        for family in ("hydro-phs", "hydro-phs-pure"):
            turbine = f"{family}-turbine"
            pump = f"{family}-pump"
            reservoir = f"{family}-reservoir"
            self.assertAlmostEqual(
                mapped.loc[pump, "p_nom_mw"] / mapped.loc[turbine, "p_nom_mw"],
                abs(base.loc[pump, "p_nom"]) / base.loc[turbine, "p_nom"],
            )
            self.assertAlmostEqual(
                mapped.loc[reservoir, "e_nom_mwh"] / mapped.loc[turbine, "p_nom_mw"],
                base.loc[reservoir, "e_nom"] / base.loc[turbine, "p_nom"],
            )

        self.assertAlmostEqual(battery.loc[0, "p_nom_mw"], 11490.0)
        self.assertAlmostEqual(battery.loc[0, "duration_h"], 2.0)
        self.assertEqual(audit["aggregation"]["modeled_hours"], 8760)
        self.assertEqual(audit["ignored_zero_sources"], ["wnd"])

    def test_written_outputs_are_accepted_by_existing_override_readers(self) -> None:
        result = extract_company_capacities(
            SOURCE,
            BASE_CAPACITIES,
            self.directory / "processed",
            bus="DE00",
            year=2030,
        )
        base = pd.read_csv(BASE_CAPACITIES)
        data = {
            "buses": pd.DataFrame({"bus_id": sorted(base["bus"].unique())}),
            "capacities": base.copy(),
            "offshore_cap": pd.Series(
                {"DE00": float(base.loc[(base.bus == "DE00") & (base.index_carrier == "offwind"), "p_nom"].iloc[0])}
            ),
        }
        apply_capacity_override(data, result.capacity_path)
        battery = read_battery_override(
            result.battery_path,
            set(data["buses"]["bus_id"]),
            {"DE00"},
        )
        mapped = data["capacities"].set_index(["bus", "index_carrier"])
        original_fr_onwind = float(
            base.loc[(base.bus == "FR00") & (base.index_carrier == "onwind"), "p_nom"].iloc[0]
        )

        self.assertTrue(result.audit_path.is_file())
        self.assertLess(mapped.loc[("DE00", "hydro-phs-pump"), "p_nom"], 0)
        self.assertAlmostEqual(mapped.loc[("DE00", "onwind"), "p_nom"], 85419.205479, places=5)
        self.assertEqual(mapped.loc[("FR00", "onwind"), "p_nom"], original_fr_onwind)
        self.assertAlmostEqual(battery.loc[0, "p_nom_mw"], 11490.0)

    def test_invalid_company_sources_fail_loudly(self) -> None:
        cases = {}

        raw, path = self.source_copy("unit.csv")
        raw.iloc[3, 1:] = "MW"
        cases["unit must be 'GW'"] = self.write_source(raw, path)

        raw, path = self.source_copy("missing_month.csv")
        raw = raw.drop(raw.index[-1])
        cases["exactly one row for every month"] = self.write_source(raw, path)

        raw, path = self.source_copy("duplicate_month.csv")
        raw.iloc[-1, 0] = raw.iloc[-2, 0]
        cases["dates must be unique"] = self.write_source(raw, path)

        raw, path = self.source_copy("negative.csv")
        raw.iloc[5, 1] = "-1,0"
        cases["must be non-negative"] = self.write_source(raw, path)

        raw, path = self.source_copy("missing_value.csv")
        raw.iloc[5, 1] = ""
        cases["must be complete"] = self.write_source(raw, path)

        raw, path = self.source_copy("unknown_symbol.csv")
        raw.iloc[0, 1] = raw.iloc[0, 1].replace(".bio.", ".mystery.")
        cases["supported schema"] = self.write_source(raw, path)

        raw, path = self.source_copy("generic_wind.csv")
        wind_column = next(
            column for column in raw.columns if ".wnd.d.c" in raw.iloc[0, column]
        )
        raw.iloc[5, wind_column] = "1,0"
        cases["must be zero"] = self.write_source(raw, path)

        for message, invalid_path in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(OverrideValidationError, message):
                    read_company_capacity_source(invalid_path, 2030)

    def test_missing_model_key_fails_loudly(self) -> None:
        base = pd.read_csv(BASE_CAPACITIES)
        base = base[~((base["bus"] == "DE00") & (base["index_carrier"] == "onwind"))]
        path = self.directory / "base.csv"
        base.to_csv(path, index=False)
        source = read_company_capacity_source(SOURCE, 2030)

        with self.assertRaisesRegex(OverrideValidationError, "missing required carrier.*onwind"):
            build_company_capacity_overrides(source, path, "DE00", 2030)


if __name__ == "__main__":
    unittest.main()
