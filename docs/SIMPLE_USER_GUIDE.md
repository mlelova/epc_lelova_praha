# Simple User Guide

All files are on your disk.  Run the commands below from the project root.

---

## 1. Set up the Python environment (first time only)

```bash
./scripts/setup.sh
```

Then activate it in every new terminal:

```bash
source venv/bin/activate
```

---

## 2. Choose your path

### Option A — use the pre-solved data (fast)

If `solved_networks_core/` and `data/tyndp2024/preprocessed/solved/` are
already on disk, skip straight to step 3.

### Option B — solve all 432 scenarios yourself (~30 hours)

Requires a Gurobi academic licence.

1. Register at <https://www.gurobi.com/features/academic-named-user-license/>
   using your university email.
2. Retrieve the licence (must be on your university network or VPN):
   ```bash
   grbgetkey <your-license-id>
   ```
3. Verify:
   ```bash
   gurobi_cl --license
   ```
4. Run the full pipeline:
   ```bash
   ./scripts/run_pipeline.sh
   ```

---

## 3. Generate all thesis figures

```bash
./scripts/run_analysis.sh
```

---

## 4. See the results

- **Figures** (JPG): `tukedip_pdflatex_utf-8/figures/`
- **Numeric tables** (ANOVA, OLS, spillover): `run_analysis.sh` has
  already written all cell outputs directly into the notebook files.
  Open them in Jupyter Lab and read the output of each cell:
  ```bash
  jupyter lab
  ```
  (Chrome or your default browser will open.  Navigate to
  `scenario_analysis/` and open the notebook — all tables and plots are
  visible without re-running.)
