"""
run_all.py — runs the Aman Prediction pipeline end to end, in dependency order.

Usage:
    python run_all.py              # run every step
    python run_all.py --from 5     # start at step 5 (earlier outputs must already exist)
    python run_all.py --only 9 10  # run just these steps

Each step runs as its own process with the same Python interpreter, so a failure
stops the run and names the script that failed.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT / "pipeline"

STEPS = [
    (1, "01_build_synthetic_data.py"),
    (2, "02_glm_refit.py"),
    (3, "03_pure_premium.py"),
    (4, "04_backtest.py"),
    (5, "05_segment_performance_by_year.py"),
    (6, "06_lever_allocation.py"),
    (7, "07_forecast_seasonal.py"),
    (8, "08_forecast_kpi_cards.py"),
    (9, "09_profit_optimizer.py"),
    (10, "10_underwriting_pnl.py"),
    (11, "11_build_alerts.py"),
]


def main():
    parser = argparse.ArgumentParser(description="Run the Aman Prediction pipeline")
    parser.add_argument("--from", dest="start", type=int, default=1, help="first step to run")
    parser.add_argument("--only", type=int, nargs="+", help="run only these step numbers")
    args = parser.parse_args()

    steps = [s for s in STEPS if (s[0] in args.only if args.only else s[0] >= args.start)]
    total_start = time.time()
    for num, script in steps:
        print(f"\n{'=' * 70}\n[{num:>2}/{len(STEPS)}] {script}\n{'=' * 70}", flush=True)
        t0 = time.time()
        result = subprocess.run([sys.executable, str(PIPELINE / script)], cwd=ROOT)
        if result.returncode != 0:
            print(f"\nFAILED at step {num}: {script} (exit code {result.returncode})")
            sys.exit(result.returncode)
        print(f"--- step {num} done in {time.time() - t0:.0f}s", flush=True)
    print(f"\nPipeline finished: {len(steps)} steps in {time.time() - total_start:.0f}s")


if __name__ == "__main__":
    main()
