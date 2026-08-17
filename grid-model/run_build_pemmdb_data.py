"""
Standalone wrapper for build_pemmdb_data.py from open-tyndp.
Generates pemmdb_profiles_2030.nc and pemmdb_capacities_2030.csv
from the PEMMDB2 Excel files in data/tyndp2024/PEMMDB2/2030/.

Usage:
    python grid-model/run_build_pemmdb_data.py
"""

import sys
import logging
from pathlib import Path
from functools import partial
from itertools import product

import numpy as np
import pandas as pd
import xarray as xr
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

# --- Paths -----------------------------------------------------------------
BASE_DIR = Path(__file__).parent.parent
# Script adds /{pyear}/ subfolder automatically, so point to parent PEMMDB2 dir
PEMMDB_DIR = BASE_DIR / "data/tyndp2024/PEMMDB2"
CARRIER_MAPPING_FN = str(BASE_DIR / "data/open-tyndp/tyndp_technology_map.csv")
BUSES_CSV = BASE_DIR / "data/open-tyndp/buses.csv"
OUT_PROFILES = BASE_DIR / "data/open-tyndp/pemmdb_profiles_2030.nc"
OUT_CAPACITIES = BASE_DIR / "data/open-tyndp/pemmdb_capacities_2030.csv"

# Climate year (CY2009 used by TYNDP)
CYEAR = 2009
PYEAR_I = 2030
PYEAR = 2030
TYNDP_SCENARIO = "NT"

# Technologies to include (from open-tyndp config for NT scenario)
PEMMDB_TECHS = [
    "Nuclear", "Hard coal", "Lignite", "Gas", "Light oil", "Heavy oil", "Oil shale",
    "Hydrogen",
    "Other Non-RES",
    "DSR",
    "Other RES",
    "Battery",
    "Hydro",
]

# -------------------------------------------------------------------------
# Add grid-model/ to sys.path so 'scripts._helpers' import in build_pemmdb_data works
sys.path.insert(0, str(BASE_DIR / "grid-model"))

# The downloaded _helpers.py is at grid-model/_helpers.py
# build_pemmdb_data.py imports from "scripts._helpers" which in the open-tyndp repo
# means scripts/_helpers.py. We create a shim package.
import types, importlib

# Create fake 'scripts' package pointing to our grid-model dir
scripts_pkg = types.ModuleType("scripts")
scripts_pkg.__path__ = [str(BASE_DIR / "grid-model")]
scripts_pkg.__package__ = "scripts"
sys.modules["scripts"] = scripts_pkg

# Now import _helpers as scripts._helpers
import importlib.util
spec = importlib.util.spec_from_file_location(
    "scripts._helpers",
    BASE_DIR / "grid-model/_helpers.py",
    submodule_search_locations=[]
)
helpers_mod = importlib.util.module_from_spec(spec)
sys.modules["scripts._helpers"] = helpers_mod
spec.loader.exec_module(helpers_mod)

# -------------------------------------------------------------------------
# Now import the actual build script's functions (not __main__)
import importlib.util as ilu

spec2 = ilu.spec_from_file_location(
    "build_pemmdb_data",
    BASE_DIR / "grid-model/build_pemmdb_data.py"
)
bpd = ilu.module_from_spec(spec2)
# Prevent __main__ block from running during import
bpd.__name__ = "build_pemmdb_data"  # not __main__
spec2.loader.exec_module(bpd)

# -------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Helper stubs for functions that need snakemake config
def get_snapshots_simple(cyear: int) -> pd.DatetimeIndex:
    return pd.date_range(
        start=f"{cyear}-01-01",
        end=f"{cyear+1}-01-01",
        freq="h",
        inclusive="left"
    )

# -------------------------------------------------------------------------
def main():
    logger.info("=== run_build_pemmdb_data standalone ===")
    logger.info(f"PEMMDB dir: {PEMMDB_DIR}")
    logger.info(f"Climate year: {CYEAR}, Planning year: {PYEAR_I}")

    # Snapshots
    sns = get_snapshots_simple(CYEAR)
    sns_year_h = get_snapshots_simple(CYEAR)

    # Nodes: from PEMMDB directory (all nodes that have a file)
    pemmdb_files = sorted((PEMMDB_DIR / str(PYEAR)).glob("PEMMDB_*_NationalTrends_2030.xlsx"))
    nodes = pd.Index([f.stem.split("_")[1] for f in pemmdb_files])
    logger.info(f"Found {len(nodes)} nodes: {list(nodes[:5])} ...")

    # Technology sheets mapping
    pemmdb_techs = PEMMDB_TECHS
    pemmdb_tech_sheets = list({bpd.PEMMDB_SHEET_MAPPING.get(t, t) for t in pemmdb_techs})
    thermal_techs = [k for k, v in bpd.PEMMDB_SHEET_MAPPING.items() if v == "Thermal"]
    logger.info(f"Tech sheets: {pemmdb_tech_sheets}")

    # Load all PEMMDB data
    func_read = partial(
        bpd.read_pemmdb_data,
        pemmdb_dir=str(PEMMDB_DIR),
        cyear=CYEAR,
        pyear=PYEAR,
        required_sheets=pemmdb_tech_sheets,
    )

    logger.info("Loading PEMMDB Excel files...")
    pemmdb_data_list = []
    for node in tqdm(nodes, desc="Loading PEMMDB data..."):
        result = func_read(node)
        if result is not None:
            pemmdb_data_list.append(result)

    pemmdb_data = {node: data for d in pemmdb_data_list for node, data in d.items()}
    logger.info(f"Loaded data for {len(pemmdb_data)} nodes")

    # Process capacities
    node_tech_sheets = list(product(nodes, pemmdb_tech_sheets))

    logger.info("Processing PEMMDB capacities...")
    pemmdb_capacities = []
    for node_tech_sheet in tqdm(node_tech_sheets, desc="Processing capacities..."):
        try:
            caps = bpd.process_pemmdb_data(
                "capacities",
                node_tech_sheet=node_tech_sheet,
                pemmdb_data=pemmdb_data,
                thermal_techs=thermal_techs,
                cyear=CYEAR,
                pyear=PYEAR,
                pyear_i=PYEAR_I,
                tyndp_scenario=TYNDP_SCENARIO,
                sns=sns,
                sns_year_h=sns_year_h,
                carrier_mapping_fn=CARRIER_MAPPING_FN,
            )
            if caps is not None:
                pemmdb_capacities.append(caps)
        except Exception as e:
            logger.warning(f"Skipping capacities for {node_tech_sheet}: {e}")

    if pemmdb_capacities:
        pemmdb_capacities_df = pd.concat(pemmdb_capacities, axis=0)
        pemmdb_capacities_df.to_csv(OUT_CAPACITIES, index=False)
        logger.info(f"Saved capacities to {OUT_CAPACITIES} ({len(pemmdb_capacities_df)} rows)")
    else:
        logger.warning("No capacities found!")

    # Process profiles
    logger.info("Processing PEMMDB profiles...")
    pemmdb_profiles = []
    for node_tech_sheet in tqdm(node_tech_sheets, desc="Processing profiles..."):
        try:
            profiles = bpd.process_pemmdb_data(
                "profiles",
                node_tech_sheet=node_tech_sheet,
                pemmdb_data=pemmdb_data,
                thermal_techs=thermal_techs,
                cyear=CYEAR,
                pyear=PYEAR,
                pyear_i=PYEAR_I,
                tyndp_scenario=TYNDP_SCENARIO,
                sns=sns,
                sns_year_h=sns_year_h,
                carrier_mapping_fn=CARRIER_MAPPING_FN,
            )
            if profiles is not None:
                pemmdb_profiles.append(profiles)
        except Exception as e:
            logger.warning(f"Skipping profiles for {node_tech_sheet}: {e}")

    if not pemmdb_profiles:
        logger.warning("No profiles found! Saving empty dataset.")
        xr.Dataset().to_netcdf(OUT_PROFILES)
        return

    pemmdb_profiles_df = pd.concat(pemmdb_profiles, axis=0)
    logger.info(f"Total profile rows: {len(pemmdb_profiles_df)}")
    logger.info(f"Index levels: {pemmdb_profiles_df.index.names}")
    logger.info(f"Carriers: {pemmdb_profiles_df.index.get_level_values('carrier').unique().tolist()}")

    ds = xr.Dataset(
        {
            "p_min_pu": (["sample"], pemmdb_profiles_df["p_min_pu"].values),
            "p_max_pu": (["sample"], pemmdb_profiles_df["p_max_pu"].values),
        },
        coords={
            level: (["sample"], pemmdb_profiles_df.index.get_level_values(level))
            for level in pemmdb_profiles_df.index.names
        },
    )
    ds.to_netcdf(OUT_PROFILES)
    logger.info(f"Saved profiles to {OUT_PROFILES}")
    logger.info("=== Done ===")


if __name__ == "__main__":
    main()
