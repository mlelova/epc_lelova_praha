from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from scenarios.solve_scenarios import solve_network


class SolveNetworkTests(unittest.TestCase):
    def test_gurobi_uses_automatic_lp_method_and_crossover(self) -> None:
        network = MagicMock()
        network.optimize.return_value = ("ok", "optimal")
        network.objective = 123.0
        network.generators = pd.DataFrame(
            {"carrier": pd.Series(dtype="object")}
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            network_path = root / "built.nc"
            output_path = root / "solved" / "forecast.nc"

            with patch(
                "scenarios.solve_scenarios.pypsa.Network",
                return_value=network,
            ):
                result = solve_network(
                    network_path=network_path,
                    output_path=output_path,
                    threads=4,
                )

        network.import_from_netcdf.assert_called_once_with(str(network_path))
        network.export_to_netcdf.assert_called_once_with(str(output_path))
        self.assertEqual(result["status"], "solved")

        optimize_options = network.optimize.call_args.kwargs
        self.assertEqual(optimize_options["solver_name"], "gurobi")
        self.assertEqual(optimize_options["io_api"], "direct")

        solver_options = optimize_options["solver_options"]
        self.assertEqual(solver_options["threads"], 4)
        self.assertEqual(solver_options["FeasibilityTol"], 1e-5)
        self.assertEqual(solver_options["OptimalityTol"], 1e-5)
        self.assertEqual(solver_options["LogToConsole"], 0)
        self.assertEqual(
            solver_options["LogFile"],
            str(output_path.parent / "forecast_gurobi.log"),
        )
        self.assertNotIn("Method", solver_options)
        self.assertNotIn("Crossover", solver_options)


if __name__ == "__main__":
    unittest.main()
