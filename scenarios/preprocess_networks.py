"""
preprocess_networks.py
======================
Extracts all time-series and static data from every solved network
and saves to data/tyndp2024/preprocessed/solved/.

Output (wide format — tag+timestamp as rows, component names as columns):
  solved/
    lmp.parquet           buses_t.marginal_price       [EUR/MWh]
    gen_p.parquet         generators_t.p               [MW]
    storage_p.parquet     storage_units_t.p            [MW]
    storage_soc.parquet   storage_units_t.state_of_charge [MWh]
    links_p0.parquet      links_t.p0                   [MW]
    static_generators.parquet     static tables saved once
    static_storage_units.parquet  from the first network
    static_buses.parquet
    static_links.parquet
    manifest.parquet      scenario metadata

Rows per time-series file: 432 scenarios × 8760 hours = 3 782 880
Numeric dtype: float32 (half the size of float64)
Memory: one network loaded at a time (streaming write via pyarrow)

Run from project root:
    python scenarios/preprocess_networks.py
    python scenarios/preprocess_networks.py --solved-dir /data/solved_networks_core --out-dir /data/preprocessed/solved
"""

import argparse
import gc
import sys
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pypsa

ROOT = Path(__file__).resolve().parent.parent

TIMESERIES = {
    "lmp": ("buses_t", "marginal_price"),
    "gen_p": ("generators_t", "p"),
    "storage_p": ("storage_units_t", "p"),
    "storage_soc": ("storage_units_t", "state_of_charge"),
    "links_p0": ("links_t", "p0"),
}

STATIC = {
    "static_generators": "generators",
    "static_storage_units": "storage_units",
    "static_buses": "buses",
    "static_links": "links",
}


def to_arrow(tag: str, df: pd.DataFrame, ref_cols: list[str] | None = None) -> pa.Table:
    """Prepend tag + timestamp, cast numerics to float32, return Arrow table.

    ref_cols: if given, reindex to this column list (fill missing with 0).
    This is needed because generators_t.p only contains generators with
    non-zero dispatch — the set of active generators differs between scenarios.
    """
    out = df.astype("float32").reset_index()
    out.columns = [str(c) for c in out.columns]
    out = out.rename(columns={out.columns[0]: "timestamp"})
    out.insert(0, "tag", tag)

    if ref_cols is not None:
        for col in ref_cols:
            if col not in out.columns:
                out[col] = 0.0
        out = out[ref_cols]  # enforce column order

    return pa.Table.from_pandas(out, preserve_index=False)


def main(solved_dir: Path, out_dir: Path) -> None:
    meta = pd.read_csv(solved_dir / "solve_results.csv")
    solved = meta[meta.status == "solved"].reset_index(drop=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    meta.to_parquet(out_dir / "manifest.parquet")

    print(f"{len(solved)} scenarios to preprocess")
    print(f"Input:  {solved_dir}")
    print(f"Output: {out_dir}\n")

    writers: dict[str, pq.ParquetWriter] = {}
    ref_cols: dict[str, list[str]] = {}  # fixed column order per time series type
    static_saved = False
    t0 = time.time()

    for i, (_, row) in enumerate(solved.iterrows()):
        tag = row["tag"]
        nc_path = solved_dir / f"{tag}.nc"
        if not nc_path.exists():
            print(f"  [{i+1}/{len(solved)}] MISSING  {tag}")
            continue

        n = pypsa.Network()
        n.import_from_netcdf(str(nc_path))

        # --- time series ---
        for name, (component, attr) in TIMESERIES.items():
            df: pd.DataFrame = getattr(getattr(n, component, None), attr, None)
            if df is None or df.empty:
                continue

            if name not in ref_cols:
                # First network defines the reference column list for this type
                tmp = df.astype("float32").reset_index()
                tmp.columns = [str(c) for c in tmp.columns]
                tmp = tmp.rename(columns={tmp.columns[0]: "timestamp"})
                ref_cols[name] = ["tag", "timestamp"] + [
                    c for c in tmp.columns if c != "timestamp"
                ]
                print(
                    f"    [schema] {name}: {len(ref_cols[name])-2} columns from {tag}"
                )

            # Warn about extra columns not captured in schema
            current_cols = [str(c) for c in df.columns.tolist()]
            extra = [c for c in current_cols if c not in ref_cols[name]]
            if extra:
                print(
                    f"    [WARNING] {name} — {tag} has {len(extra)} extra cols not in schema: {extra[:5]}{'...' if len(extra) > 5 else ''}"
                )

            table = to_arrow(tag, df, ref_cols=ref_cols[name])
            if name not in writers:
                writers[name] = pq.ParquetWriter(
                    str(out_dir / f"{name}.parquet"), table.schema, compression="snappy"
                )
            writers[name].write_table(table)

        # --- static tables — written once from the first network ---
        if not static_saved:
            for fname, attr in STATIC.items():
                tbl: pd.DataFrame = getattr(n, attr, pd.DataFrame())
                if tbl is None or tbl.empty:
                    continue
                tbl = tbl.copy()
                for col in tbl.select_dtypes("object").columns:
                    tbl[col] = tbl[col].astype(str)
                tbl.to_parquet(out_dir / f"{fname}.parquet")
            static_saved = True

        del n
        gc.collect()

        elapsed = time.time() - t0
        eta_min = (len(solved) - i - 1) / ((i + 1) / elapsed) / 60
        print(f"  [{i+1}/{len(solved)}] {tag}  (ETA {eta_min:.0f} min)")
        sys.stdout.flush()

    for w in writers.values():
        w.close()

    print(f"\nDone in {(time.time()-t0)/60:.1f} min")
    print("\nFile sizes:")
    for f in sorted(out_dir.glob("*.parquet")):
        print(f"  {f.name:<40} {f.stat().st_size/1e6:>7.1f} MB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preprocess solved networks → Parquet")
    parser.add_argument(
        "--solved-dir",
        type=Path,
        default=ROOT / "solved_networks_core",
        help="Directory with solved .nc files and solve_results.csv",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "data" / "tyndp2024" / "preprocessed" / "solved",
        help="Output directory for Parquet files",
    )
    args = parser.parse_args()
    main(args.solved_dir, args.out_dir)
