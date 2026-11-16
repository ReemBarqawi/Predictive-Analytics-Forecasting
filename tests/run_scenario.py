"""
Run the whole pipeline on a made-up company (tests/make_company.py) in a scratch folder, in run_all.py's order,
ending with the reconcile gate (step 13). Nothing touches the real extracts, results or run log.

    .venv/Scripts/python.exe tests/run_scenario.py full          # one scenario, prints where its files are
    .venv/Scripts/python.exe tests/run_scenario.py --list

Each scenario says whether the run must finish ("ok") or stop at a named step with a clear message ("stop at N").
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from make_company import make_company  # noqa: E402

# name: (make_company options, expected outcome)
SCENARIOS = {
    "full": (dict(), "ok"),
    "motor_only": (dict(lines=False), "ok"),
    "no_vehicles": (dict(vehicles=False, lines=False), "ok"),
    "no_triangles": (dict(triangles=False, lines=False), "ok"),
    "one_class": (dict(classes=("Motor Private",), lines=False), "ok"),
    "short_history": (dict(years=(2021, 2024), lines=False), "ok"),
    "missing_year": (dict(skip_year=2021, lines=False), "ok"),
    "no_claims_file": (dict(lines=False, drop=("claims",)), "stop at 23"),
}


def steps() -> list[tuple[int, str]]:
    """run_all.py's order for an extract that is already in place: validation, preparation, the models, the gate."""
    sys.path.insert(0, str(ROOT))
    import run_all
    order = [s for s in run_all.STEPS["oracle"] if s[0] != 0]
    return order + [run_all.GATE]


def run(name: str, workdir: Path | None = None) -> tuple[str, Path, str]:
    """Returns (outcome, folder, last log lines): outcome is "ok" or "stop at N"."""
    opts, _ = SCENARIOS[name]
    opts = dict(opts)
    drop = opts.pop("drop", ())
    work = Path(workdir or tempfile.mkdtemp(prefix=f"aman_{name}_"))
    raw = work / "raw"
    make_company(raw, **opts)
    for f in drop:
        (raw / f"{f}.parquet").unlink(missing_ok=True)
    local = work / "config.local.toml"
    local.write_text("\n".join([
        "[profiles.oracle]",
        f'raw_dir = "{raw.as_posix()}"',
        f'output_root = "{(work / "out").as_posix()}"',
        f'models_dir = "{(work / "out" / "models").as_posix()}"',
        f'reports_dir = "{(work / "out" / "reports").as_posix()}"', ""]), encoding="utf-8")
    env = {**os.environ, "AMAN_SOURCE": "oracle", "AMAN_LOCAL_CONFIG": str(local), "PYTHONIOENCODING": "utf-8"}
    log = work / "run.log"
    outcome = "ok"
    with open(log, "w", encoding="utf-8") as out:
        for num, script in steps():
            t0 = time.time()
            r = subprocess.run([sys.executable, str(ROOT / "pipeline" / script)], cwd=ROOT, env=env,
                               stdout=out, stderr=subprocess.STDOUT)
            out.write(f"--- step {num} exit {r.returncode} in {time.time() - t0:.0f}s\n")
            out.flush()
            if r.returncode != 0:
                outcome = f"stop at {num}"
                break
    tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:])
    return outcome, work, tail


DEVELOPMENT_TOLERANCE = 0.25    # developed current-year claims of the other lines within this of the expected
FORECAST_TOLERANCE = 0.30       # their next-12-month forecast claims within this of the expected


def lines_development_problem(work: Path) -> str | None:
    """The other lines' developed claims of the current year against the made-up company's expected claims (what
    it is made with; the claims not yet reported or not yet happened can only be estimated at their expected
    value): nearer to it than the recorded claims, and within DEVELOPMENT_TOLERANCE. The claims actually drawn
    are shown for information. None when fine, or when the scenario has no truth file."""
    import pandas as pd
    truth_file = work / "raw" / "_truth_lines.parquet"
    seg_file = work / "out" / "02_facts" / "fact_line_segment_performance_by_year.csv"
    if not truth_file.exists():
        return None
    if not seg_file.exists():
        return f"no {seg_file.name}"
    seg = pd.read_csv(seg_file)
    year = int(seg["POLICY_YEAR"].max())
    seg = seg[seg["POLICY_YEAR"] == year]
    truth = pd.read_parquet(truth_file)
    truth = truth[(truth["POLICY_YEAR"] == year) & truth["CLASS_DESC"].isin(set(seg["CLASS_DESC"]))]
    want, drawn = truth["EXPECTED_CLAIMS"].sum(), truth["TRUE_CLAIMS"].sum()
    dev, rec = seg["ULTIMATE_CLAIMS"].sum(), seg["ACTUAL_ULTIMATE_CLAIMS"].sum()
    msg = f"{year}: expected {want:,.0f} (drawn {drawn:,.0f}), developed {dev:,.0f}, recorded {rec:,.0f}"
    print(f"other lines' development {msg}")
    if want <= 0:
        return f"no expected claims ({msg})"
    if abs(dev - want) >= abs(rec - want) or abs(dev - want) / want > DEVELOPMENT_TOLERANCE:
        return f"other lines' development is off: {msg}"
    # Forecast (step 22): next year's claims of the forecast classes against the same expected claims (the company
    # is made with the same volume and claims every year)
    fc_file = work / "out" / "02_facts" / "fact_line_forecast.csv"
    if not fc_file.exists():
        return f"no {fc_file.name}"
    fc = pd.read_csv(fc_file)
    fc = fc[fc["FORECAST_HORIZON"] == "12M"]
    exp = pd.read_parquet(truth_file)
    exp = exp[(exp["POLICY_YEAR"] == year) & exp["CLASS_DESC"].isin(set(fc["CLASS_DESC"]))]["EXPECTED_CLAIMS"].sum()
    got = fc["FORECAST_CLAIMS"].sum()
    msg = f"next 12 months: expected {exp:,.0f}, forecast {got:,.0f} in {fc['CLASS_DESC'].nunique()} classes"
    print(f"other lines' forecast {msg}")
    if exp <= 0 or abs(got - exp) / exp > FORECAST_TOLERANCE:
        return f"other lines' forecast is off: {msg}"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list or not a.names:
        for n, (o, want) in SCENARIOS.items():
            print(f"{n:<16} expect {want:<12} {o}")
        return 0
    bad = 0
    for n in a.names:
        t0 = time.time()
        outcome, work, tail = run(n)
        want = SCENARIOS[n][1]
        problem = lines_development_problem(work) if outcome == "ok" else None
        ok = outcome == want and problem is None
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'} {n}: {outcome} (expected {want}) in {time.time() - t0:.0f}s  [{work}]")
        if problem:
            print(problem)
        if not ok:
            print(tail)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
