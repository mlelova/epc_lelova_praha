from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from remake.build_network import apply_generator_availability
from remake.company_availability import (
    _bounded_daily_shape,
    build_company_availability_overrides,
    read_company_availability_source,
)
from remake.errors import OverrideValidationError
from remake.generation_comparison import compare_generation
from remake.input_data import MODEL_SNAPSHOTS, load_hourly_input
from remake.load_network import read_generator_availability_override


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "company_data" / "available_cap.csv"
CAPACITY_OVERRIDE = (
    ROOT / "company_data" / "processed" / "capacity_override_de00_2030.csv"
)
DATA_DIR = ROOT / "data" / "open-tyndp"


class CompanyAvailabilitySourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def mutated_source(self, mutate) -> Path:
        raw = pd.read_csv(SOURCE, header=None, dtype=str, keep_default_na=False)
        mutate(raw)
        path = self.directory / "available_cap.csv"
        raw.to_csv(path, header=False, index=False)
        return path

    def test_real_source_metadata_year_and_empty_production_columns(self) -> None:
        source = read_company_availability_source(SOURCE, 2030)

        self.assertEqual(source.effective_date, "2026-08-15T18:24:00")
        self.assertEqual(source.tag, "EPC_LT_Model")
        self.assertEqual(source.timezone, "CET")
        self.assertEqual(source.available_gw.shape, (365, 19))
        self.assertEqual(source.installed_gw.shape, (365, 19))
        self.assertEqual(source.production_gwh.shape, (365, 19))
        self.assertEqual(source.available_gw.index[0], pd.Timestamp("2030-01-01"))
        self.assertEqual(source.available_gw.index[-1], pd.Timestamp("2030-12-31"))
        self.assertEqual(len(source.ignored_empty_symbols), 10)
        self.assertTrue(
            all(".pro." in symbol for symbol in source.ignored_empty_symbols)
        )

    def test_rejects_invalid_unit(self) -> None:
        path = self.mutated_source(
            lambda raw: raw.__setitem__(
                1, raw[1].where(raw.index != 3, "MW")
            )
        )
        with self.assertRaisesRegex(OverrideValidationError, "unit must be 'GW'"):
            read_company_availability_source(path, 2030)

    def test_rejects_invalid_timezone_metadata(self) -> None:
        def mutate(raw: pd.DataFrame) -> None:
            raw.iloc[4, 1:] = "UTC"

        path = self.mutated_source(mutate)
        with self.assertRaisesRegex(OverrideValidationError, "timezone must be 'CET'"):
            read_company_availability_source(path, 2030)

    def test_rejects_invalid_date_format(self) -> None:
        def mutate(raw: pd.DataFrame) -> None:
            raw.iloc[5, 0] = "2030-01-01"

        path = self.mutated_source(mutate)
        with self.assertRaisesRegex(OverrideValidationError, "MM/DD/YYYY HH:MM"):
            read_company_availability_source(path, 2030)

    def test_rejects_missing_numeric_value(self) -> None:
        def mutate(raw: pd.DataFrame) -> None:
            raw.iloc[5, 1] = ""

        path = self.mutated_source(mutate)
        with self.assertRaisesRegex(OverrideValidationError, "complete and finite"):
            read_company_availability_source(path, 2030)

    def test_rejects_unknown_nonempty_symbol(self) -> None:
        def mutate(raw: pd.DataFrame) -> None:
            raw.iloc[0, 1] = "genscape/power/supply/de.cap_avail.bl.unknown.d.c"

        path = self.mutated_source(mutate)
        with self.assertRaisesRegex(OverrideValidationError, "Unsupported.*unknown"):
            read_company_availability_source(path, 2030)

    def test_rejects_unavailable_year_and_leap_year(self) -> None:
        with self.assertRaisesRegex(OverrideValidationError, "every day of 2033"):
            read_company_availability_source(SOURCE, 2033)
        with self.assertRaisesRegex(OverrideValidationError, "non-leap"):
            read_company_availability_source(SOURCE, 2028)


class CompanyAvailabilityMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = read_company_availability_source(SOURCE, 2030)
        (
            cls.vre,
            cls.generators,
            cls.production,
            cls.audit,
        ) = build_company_availability_overrides(
            cls.source,
            CAPACITY_OVERRIDE,
            DATA_DIR,
            "DE00",
            2030,
            2009,
        )
        capacity = pd.read_csv(CAPACITY_OVERRIDE)
        cls.capacities = capacity.set_index("index_carrier")["p_nom_mw"]

    def test_vre_profiles_preserve_daily_available_energy(self) -> None:
        source_by_technology = {
            "wind_onshore": "wnd_on",
            "wind_offshore": "wnd_off",
        }
        carrier_by_technology = {
            "wind_onshore": "onwind",
            "wind_offshore": "offwind",
        }
        for technology, source_slug in source_by_technology.items():
            values = self.vre.loc[
                self.vre["technology"].eq(technology), "p_max_pu"
            ].to_numpy().reshape(365, 24)
            expected = (
                self.source.available_gw[source_slug].to_numpy()
                * 1000.0
                / self.capacities[carrier_by_technology[technology]]
            )
            np.testing.assert_allclose(values.mean(axis=1), expected, atol=1e-8)

        solar = self.vre.loc[
            self.vre["technology"].isin(["solar_utility", "solar_rooftop"])
        ]
        daily_potential_mwh = np.zeros(365)
        for technology, carrier in (
            ("solar_utility", "solar-pv-utility"),
            ("solar_rooftop", "solar-pv-rooftop"),
        ):
            values = solar.loc[
                solar["technology"].eq(technology), "p_max_pu"
            ].to_numpy().reshape(365, 24)
            daily_potential_mwh += values.sum(axis=1) * self.capacities[carrier]
        expected_mwh = self.source.available_gw["spv"].to_numpy() * 1000.0 * 24.0
        np.testing.assert_allclose(daily_potential_mwh, expected_mwh, atol=1e-3)

    def test_vre_profiles_are_bounded_and_solar_stays_zero_at_night(self) -> None:
        self.assertTrue(self.vre["p_max_pu"].between(0.0, 1.0).all())
        for technology in ("solar_utility", "solar_rooftop"):
            base = load_hourly_input(DATA_DIR, technology, 2009)["DE00"].to_numpy()
            shaped = self.vre.loc[
                self.vre["technology"].eq(technology), "p_max_pu"
            ].to_numpy()
            self.assertTrue(np.equal(shaped[base == 0], 0.0).all())

    def test_dispatchable_direct_and_aggregate_mappings(self) -> None:
        self.assertEqual(
            list(self.generators.columns),
            ["timestamp", "bus", "index_carrier", "p_max_pu"],
        )
        expected = {
            "gas-ccgt": self.source.available_gw["ccgt"]
            * 1000.0
            / self.capacities["gas-ccgt"],
            "gas-ocgt": self.source.available_gw["gt"]
            * 1000.0
            / self.capacities["gas-ocgt"],
            "coal": self.source.available_gw["coal"]
            * 1000.0
            / self.capacities["coal"],
            "lignite": self.source.available_gw["lig"]
            * 1000.0
            / self.capacities["lignite"],
            "oil-light": self.source.available_gw["oil"]
            * 1000.0
            / self.capacities["oil-light"],
            "gas-conv": (
                self.source.available_gw["engine"]
                + self.source.available_gw["gas_boiler"]
            )
            * 1000.0
            / (
                self.capacities["gas-conv"]
                + self.capacities[
                    "chp-gas-conventional-old-1-other-128.4eur"
                ]
            ),
            "other-res": (
                self.source.available_gw["bio"]
                + self.source.available_gw["geo"]
                + self.source.available_gw["waste"]
            )
            * 1000.0
            / self.capacities["other-res"],
        }
        for carrier, daily_expected in expected.items():
            hourly = self.generators.loc[
                self.generators["index_carrier"].eq(carrier), "p_max_pu"
            ].to_numpy()
            np.testing.assert_allclose(
                hourly.reshape(365, 24)[:, 0],
                daily_expected.clip(0.0, 1.0).to_numpy(),
                atol=1e-8,
            )
            self.assertTrue(
                np.equal(hourly.reshape(365, 24), hourly.reshape(365, 24)[:, :1]).all()
            )

        chp = self.generators.loc[
            self.generators["index_carrier"].str.startswith(
                "chp-gas-conventional"
            ),
            "p_max_pu",
        ].to_numpy()
        gas_conv = self.generators.loc[
            self.generators["index_carrier"].eq("gas-conv"), "p_max_pu"
        ].to_numpy()
        np.testing.assert_array_equal(chp, gas_conv)

    def test_production_reference_and_clipping_audit(self) -> None:
        annual = self.production.groupby("technology")["production_gwh"].sum() / 1000.0
        self.assertAlmostEqual(annual["onwind"], 149.16249, places=5)
        self.assertAlmostEqual(annual["offwind"], 72.64355, places=5)
        self.assertAlmostEqual(annual["solar"], 149.39050, places=5)
        self.assertAlmostEqual(
            annual[["onwind", "offwind", "solar"]].sum(), 371.19654, places=5
        )
        self.assertEqual(self.audit["clipping"]["event_count"], 31)
        self.assertEqual(
            {event["label"] for event in self.audit["clipping"]["events"]},
            {"lignite"},
        )
        self.assertEqual(
            self.audit["source_consistency"][
                "availability_above_daily_installed_event_count"
            ],
            6,
        )
        self.assertEqual(
            {
                event["source_slug"]
                for event in self.audit["source_consistency"][
                    "availability_above_daily_installed_events"
                ]
            },
            {"lig"},
        )

    def test_bounded_shape_exact_mean_and_zero_profile_failure(self) -> None:
        base = np.r_[np.zeros(8), np.linspace(0.1, 1.0, 8), np.zeros(8)]
        shaped = _bounded_daily_shape(base, 0.2)
        self.assertAlmostEqual(float(shaped.mean()), 0.2, places=10)
        self.assertTrue(np.equal(shaped[base == 0], 0.0).all())
        self.assertTrue(np.logical_and(shaped >= 0, shaped <= 1).all())
        with self.assertRaisesRegex(OverrideValidationError, "cannot be represented"):
            _bounded_daily_shape(np.zeros(24), 0.1)


class GeneratorAvailabilityOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.capacities = pd.DataFrame(
            {
                "bus": ["DE00"],
                "index_carrier": ["gas-ccgt"],
                "p_nom": [100.0],
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_override(self, carrier: str = "gas-ccgt", rows: int = 8760) -> Path:
        path = self.directory / "generator_availability.csv"
        pd.DataFrame(
            {
                "snapshot": MODEL_SNAPSHOTS[:rows],
                "bus": "DE00",
                "index_carrier": carrier,
                "p_max_pu": 0.75,
            }
        ).to_csv(path, index=False)
        return path

    def test_reads_and_applies_complete_override(self) -> None:
        override = read_generator_availability_override(
            self.write_override(), self.capacities
        )
        network = SimpleNamespace(
            snapshots=MODEL_SNAPSHOTS,
            generators=pd.DataFrame(index=["DE00-gas-ccgt"]),
            generators_t=SimpleNamespace(
                p_max_pu=pd.DataFrame(index=MODEL_SNAPSHOTS)
            ),
        )
        apply_generator_availability(network, override)
        self.assertTrue(
            network.generators_t.p_max_pu["DE00-gas-ccgt"].eq(0.75).all()
        )

    def test_rejects_unknown_and_incomplete_targets(self) -> None:
        with self.assertRaisesRegex(OverrideValidationError, "Unknown positive-capacity"):
            read_generator_availability_override(
                self.write_override("coal"), self.capacities
            )
        with self.assertRaisesRegex(OverrideValidationError, "every 2030 hour"):
            read_generator_availability_override(
                self.write_override(rows=8759), self.capacities
            )

    def test_rejects_empty_override(self) -> None:
        path = self.directory / "empty.csv"
        pd.DataFrame(
            columns=["timestamp", "bus", "index_carrier", "p_max_pu"]
        ).to_csv(path, index=False)
        with self.assertRaisesRegex(OverrideValidationError, "is empty"):
            read_generator_availability_override(path, self.capacities)


class GenerationComparisonTests(unittest.TestCase):
    def test_reports_mapped_metrics_and_unmapped_generation(self) -> None:
        snapshots = pd.date_range("2030-01-01", periods=48, freq="h")
        network = SimpleNamespace(
            snapshots=snapshots,
            buses=pd.DataFrame(index=["DE00"]),
            generators=pd.DataFrame(
                {
                    "bus": ["DE00", "DE00", "DE00"],
                    "carrier": ["onwind", "slack", "hydrogen"],
                },
                index=["DE00-onwind", "DE00-slack", "DE00-hydrogen"],
            ),
            generators_t=SimpleNamespace(
                p=pd.DataFrame(
                    {
                        "DE00-onwind": 100.0,
                        "DE00-slack": 2.0,
                        "DE00-hydrogen": 3.0,
                    },
                    index=snapshots,
                )
            ),
            storage_units=pd.DataFrame(
                {"bus": ["DE00"], "carrier": ["battery"]},
                index=["DE00-battery"],
            ),
            storage_units_t=SimpleNamespace(
                p=pd.DataFrame({"DE00-battery": 5.0}, index=snapshots)
            ),
        )
        dates = pd.date_range("2030-01-01", periods=2, freq="D")
        reference = pd.concat(
            [
                pd.DataFrame(
                    {
                        "date": dates,
                        "technology": "onwind",
                        "production_gwh": 2.0,
                    }
                ),
                pd.DataFrame(
                    {
                        "date": dates,
                        "technology": "battery",
                        "production_gwh": 0.1,
                    }
                ),
            ],
            ignore_index=True,
        )

        aligned, report = compare_generation(network, reference, "DE00")

        onwind = aligned.loc[aligned["technology"].eq("onwind")]
        self.assertTrue(onwind["model_gwh"].eq(2.4).all())
        self.assertAlmostEqual(report["by_technology"]["onwind"]["bias_twh"], 0.0008)
        self.assertAlmostEqual(
            report["unmapped_model_generation_twh"]["hydrogen"], 0.000144
        )
        self.assertAlmostEqual(
            report["unmapped_model_generation_twh"]["slack"], 0.000096
        )


if __name__ == "__main__":
    unittest.main()
