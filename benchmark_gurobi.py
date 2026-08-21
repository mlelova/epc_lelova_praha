import pypsa
import time
import statistics
import gc

# ============================================================
# SETTINGS
# ============================================================

NETWORK_FILE = "thesis_baseline_cy2009.nc"

THREAD_COUNTS = [1, 2, 3, 4]

# Number of solves for each thread count
RUNS = 1


# ============================================================
# LOAD NETWORK
# ============================================================

print(f"Loading: {NETWORK_FILE}")

n = pypsa.Network(NETWORK_FILE)

print("Network loaded.")


# ============================================================
# BUILD MODEL ONCE
# ============================================================
# This creates the Linopy optimization model but DOES NOT
# solve it. Therefore model-building time is excluded from
# the benchmark.
# ============================================================

print("\nBuilding optimization model...")

n.optimize.create_model()

print(f"Variables:   {n.model.nvars:,}")
print(f"Constraints: {n.model.ncons:,}")
print("Model built.\n")


# ============================================================
# BENCHMARK GUROBI
# ============================================================

results = {}

for threads in THREAD_COUNTS:

    times = []

    print("=" * 60)
    print(f"Testing Gurobi with Threads={threads}")
    print("=" * 60)

    for run in range(1, RUNS + 1):

        # Reduce unrelated Python garbage collection activity
        gc.collect()

        start = time.perf_counter()

        status, condition = n.model.solve(
            solver_name="gurobi",
            threads=threads,
        )

        elapsed = time.perf_counter() - start

        times.append(elapsed)

        print(
            f"Run {run}/{RUNS}: "
            f"{elapsed:.3f} s "
            f"[{status}, {condition}]"
        )

    results[threads] = times

    print()


# ============================================================
# RESULTS
# ============================================================

print("\n")
print("=" * 75)
print("GUROBI THREAD BENCHMARK")
print("=" * 75)

print(
    f"{'Threads':>8}"
    f"{'Mean':>12}"
    f"{'Median':>12}"
    f"{'Min':>12}"
    f"{'Max':>12}"
    f"{'Speedup':>12}"
)

print("-" * 75)

baseline = statistics.median(results[1])

best_threads = None
best_median = float("inf")

for threads in THREAD_COUNTS:

    times = results[threads]

    mean = statistics.mean(times)
    median = statistics.median(times)
    minimum = min(times)
    maximum = max(times)

    speedup = baseline / median

    if median < best_median:
        best_median = median
        best_threads = threads

    print(
        f"{threads:>8}"
        f"{mean:>12.3f}"
        f"{median:>12.3f}"
        f"{minimum:>12.3f}"
        f"{maximum:>12.3f}"
        f"{speedup:>11.2f}x"
    )


# ============================================================
# RECOMMENDATION
# ============================================================

print("\n" + "=" * 75)

print(
    f"Fastest median: Threads={best_threads} "
    f"({best_median:.3f} seconds)"
)

print(
    f"\nUse:\n\n"
    f'solver_options={{"Threads": {best_threads}}}'
)

print("=" * 75)