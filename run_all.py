"""
run_all.py — runs the Aman Prediction pipeline end to end, in dependency order.

    python run_all.py                          # source from config.toml ([run] source)
    python run_all.py --source synthetic       # demo data (Excel files in ORIGINAL_DATA/)
    python run_all.py --source oracle          # company warehouse: extract, prepare, models
    python run_all.py --source oracle --skip-extract   # reuse the last extract in data/raw/
    python run_all.py --from 5                 # start at step 5 (earlier outputs must exist)
    python run_all.py --only 9 10              # run just these steps
    python run_all.py --source oracle --check-connection   # test the warehouse login only

Results go to the output folder of the chosen source (config.toml [profiles]); point Power BI's
DataFolder parameter at it. Every run is recorded in data/run_log.csv.
"""
import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT / "pipeline"
RUN_LOG = ROOT / "data" / "run_log.csv"

MODEL_STEPS = [
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
STEPS = {
    "synthetic": [(1, "01_build_synthetic_data.py")] + MODEL_STEPS,
    "oracle": [(0, "00_extract_oracle.py"), (1, "01_prepare_real.py")] + MODEL_STEPS,
}
KEY_TABLES = ["fact_policy", "fact_claim", "fact_earned_premium", "fact_segment_performance",
              "fact_optimal_scenarios", "fact_alerts"]


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "pipeline", "config.toml"],
                               cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return out + ("+changes" if dirty else "")
    except Exception:
        return "unknown"


def row_count(path: Path) -> int | str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f) - 1
    except FileNotFoundError:
        return ""


def write_run_log(source: str, steps: list, status: str, seconds: float) -> None:
    os.environ["AMAN_SOURCE"] = source
    sys.path.insert(0, str(PIPELINE))
    from settings import get_settings   # after AMAN_SOURCE is set
    s = get_settings()
    row = {"run_at": datetime.now().isoformat(timespec="seconds"), "source": source,
           "status": status, "steps": " ".join(str(n) for n, _ in steps),
           "seconds": round(seconds), "git": git_commit(), "output_root": str(s.output_root)}
    row.update({t: row_count(s.facts_dir / f"{t}.csv") for t in KEY_TABLES})
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not RUN_LOG.exists()
    with open(RUN_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    print(f"Run recorded in {RUN_LOG}")


def main():
    parser = argparse.ArgumentParser(description="Run the Aman Prediction pipeline")
    parser.add_argument("--source", choices=sorted(STEPS), help="data source (default: config.toml)")
    parser.add_argument("--from", dest="start", type=int, default=0, help="first step to run")
    parser.add_argument("--only", type=int, nargs="+", help="run only these step numbers")
    parser.add_argument("--skip-extract", action="store_true", help="oracle: reuse data/raw/")
    parser.add_argument("--check-connection", action="store_true",
                        help="oracle: test the warehouse login and count rows, then stop")
    args = parser.parse_args()

    if args.source:
        os.environ["AMAN_SOURCE"] = args.source
    sys.path.insert(0, str(PIPELINE))
    from settings import get_settings
    source = get_settings().source

    if args.check_connection:
        if source != "oracle":
            sys.exit("--check-connection needs --source oracle")
        sys.exit(subprocess.run([sys.executable, str(PIPELINE / "00_extract_oracle.py"), "--check"],
                                cwd=ROOT).returncode)

    steps = [s for s in STEPS[source] if (s[0] in args.only if args.only else s[0] >= args.start)]
    if args.skip_extract:
        steps = [s for s in steps if s[0] != 0]
    print(f"Source: {source}   steps: {' '.join(str(n) for n, _ in steps)}")

    total_start, status = time.time(), "ok"
    for num, script in steps:
        print(f"\n{'=' * 70}\n[{num:>2}] {script}\n{'=' * 70}", flush=True)
        t0 = time.time()
        result = subprocess.run([sys.executable, str(PIPELINE / script)], cwd=ROOT)
        if result.returncode != 0:
            print(f"\nFAILED at step {num}: {script} (exit code {result.returncode})")
            status = f"failed at step {num}"
            break
        print(f"--- step {num} done in {time.time() - t0:.0f}s", flush=True)
    seconds = time.time() - total_start
    print(f"\nPipeline {status}: {len(steps)} steps planned, {seconds:.0f}s")
    write_run_log(source, steps, status, seconds)
    sys.exit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
