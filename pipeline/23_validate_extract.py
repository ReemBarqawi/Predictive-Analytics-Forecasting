"""
pipeline/23_validate_extract.py
===============================
The first check of a run: does the extract keep the data contract (pipeline/contract.py, EXTRACTS)? Runs before
anything is built from it, the same way for every company, and either lets the run go on or stops it with a list
of what to fix in the extract.

Per file:     present (a needed file missing stops the run; an optional one logs what is lost without it),
              required columns present, recommended ones present (else their default is used),
              typed columns readable (dates in range, numbers numeric), required columns filled,
              keys unique.
Across files: claims finding their policy (key + policy year), vehicles finding their policy-year,
              at least one complete policy year with both premium and claims, the data's cut-off.

STOP = the results would be wrong or empty (the run stops here, exit code 1); WARN = reported, the run goes on;
PASS. Limits are method constants in contract.VALIDATION.

Outputs: <facts>/fact_extract_validation.csv, <reports>/extract_validation_report.md
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from contract import EXTRACTS, VALIDATION as V
from settings import S

results: list[dict] = []


def _log(m: str) -> None:
    print(f"[validate] {m}", flush=True)


def add(file: str, check: str, status: str, value=None, limit=None, note: str = "") -> None:
    results.append({"FILE": file, "CHECK": check, "STATUS": status, "VALUE": value, "LIMIT": limit, "NOTE": note})


def readable(s: pd.Series, kind: str) -> pd.Series:
    """True where a value can be used as its contract type (empty values count as unreadable)."""
    if kind == "date":
        d = s if pd.api.types.is_datetime64_any_dtype(s) else pd.to_datetime(s, errors="coerce")
        lo, hi = (pd.Timestamp(x) for x in V["date_range"])
        return d.notna() & (d >= lo) & (d <= hi)
    if kind in ("number", "int"):
        return pd.to_numeric(s, errors="coerce").notna()
    return s.notna() & (s.astype(str).str.strip() != "")


def check_file(name: str, spec: dict) -> pd.DataFrame | None:
    path = S.raw_dir / f"{name}.parquet"
    if not path.exists():
        if spec["needed"]:
            add(name, "file present", "STOP", note=f"missing: {spec['what']} (sql/{name}.sql)")
        else:
            add(name, "file present", "WARN", note=f"not extracted: {spec['without']}")
        return None
    df = pd.read_parquet(path)
    df.columns = [c.upper() for c in df.columns]
    add(name, "rows", "PASS" if len(df) else ("STOP" if spec["needed"] else "WARN"), len(df),
        note="" if len(df) else "the extract returned no rows")
    missing = [c for c in spec["required"] if c not in df.columns]
    add(name, "required columns", "STOP" if missing else "PASS", len(spec["required"]) - len(missing),
        len(spec["required"]), "missing: " + ", ".join(missing) if missing else "")
    absent = [c for c in spec["recommended"] if c not in df.columns]
    if absent:
        add(name, "recommended columns", "WARN", len(spec["recommended"]) - len(absent), len(spec["recommended"]),
            "not provided, defaults used: " + ", ".join(absent))
    if not len(df):
        return df
    for col, kind in spec["types"].items():
        if col not in df.columns:
            continue
        s = df[col]
        empty = s.isna() | (s.astype(str).str.strip() == "")
        unreadable = ~empty & ~readable(s, kind)
        req = col in spec["required"]
        # A required column must be filled and readable; an optional one may be empty (e.g. no close date on an
        # open claim), but what it holds must be readable
        bad = float((empty | unreadable).mean()) if req else float(unreadable.mean())
        if req and bad > V["stop_bad_required"]:
            status = "STOP"
        elif bad > V["warn_bad"]:
            status = "WARN"
        else:
            continue
        add(name, f"{col} {'filled and ' if req else ''}readable as {kind}", status, round(bad, 4),
            V["stop_bad_required"] if req else V["warn_bad"],
            f"{float(empty.mean()):.2%} empty, {float(unreadable.mean()):.2%} unreadable"
            + (f" (dates outside {V['date_range'][0][:4]}-{V['date_range'][1][:4]} count as unreadable)" if kind == "date" else ""))
    key = [c for c in spec["key"] if c in df.columns]
    if key:
        dup = float(df.duplicated(key).mean())
        add(name, "key unique (" + " + ".join(key) + ")",
            "STOP" if dup > V["stop_duplicate_key"] else ("WARN" if dup > 0 else "PASS"), round(dup, 5),
            V["stop_duplicate_key"], f"{dup:.2%} duplicate rows" if dup else "")
    return df


def match_share(child: pd.DataFrame, parent: pd.DataFrame, key: list[str]) -> float:
    k = parent[key].drop_duplicates().assign(_hit=1)
    m = child[key].merge(k, on=key, how="left")["_hit"]
    return float(m.notna().mean()) if len(m) else np.nan


def main() -> int:
    if S.raw_dir is None:
        _log("no raw folder for this source (synthetic data is built, not extracted): nothing to check")
        return 0
    data = {name: check_file(name, spec) for name, spec in EXTRACTS.items()}
    pol, clm = data.get("policies"), data.get("claims")

    # Across files
    key = ["POLICY_NO", "POLICY_YEAR"]
    if pol is not None and clm is not None and all(c in pol for c in key) and all(c in clm for c in key):
        share = match_share(clm.astype({c: str for c in key}), pol.astype({c: str for c in key}), key)
        status = "STOP" if share < V["stop_claim_match"] else ("WARN" if share < V["warn_claim_match"] else "PASS")
        add("claims", "claims find their policy (key + year)", status, round(share, 4), V["warn_claim_match"],
            f"{share:.1%} matched" + ("; the policy key in sql/claims.sql may not agree with sql/policies.sql" if status != "PASS" else ""))
    veh = data.get("vehicles")
    if veh is not None and pol is not None and all(c in veh for c in key) and all(c in pol for c in key):
        share = match_share(veh.astype({c: str for c in key}), pol.astype({c: str for c in key}), key)
        add("vehicles", "vehicles find their policy-year", "WARN" if share < V["warn_vehicle_match"] else "PASS",
            round(share, 4), V["warn_vehicle_match"], f"{share:.1%} matched")
    lp, lc = data.get("lines_policies"), data.get("lines_claims")
    if lp is not None and lc is not None and all(c in lp for c in key) and all(c in lc for c in key):
        share = match_share(lc.astype({c: str for c in key}), lp.astype({c: str for c in key}), key)
        add("lines_claims", "claims find their policy (key + year)",
            "WARN" if share < V["warn_claim_match"] else "PASS", round(share, 4), V["warn_claim_match"],
            f"{share:.1%} matched by key; step 20 tries number + year + class next and leaves out incomplete years")
    if clm is not None and "ACC_DATE" in clm:
        acc = pd.to_datetime(clm["ACC_DATE"], errors="coerce")
        acc = acc[(acc >= pd.Timestamp(V["date_range"][0])) & (acc <= pd.Timestamp(V["date_range"][1]))]
        if acc.notna().any():
            end = acc.max()
            complete = end.year - 1 if end < pd.Timestamp(year=end.year, month=12, day=31) else end.year
            add("claims", "data cut-off (last accident date)", "PASS", str(end.date()),
                note=f"current year {complete}: the last policy year with claims to its end")
            if pol is not None and "POLICY_YEAR" in pol and "POLICY_YEAR" in clm:
                prem_years = set(pd.to_numeric(pol["POLICY_YEAR"], errors="coerce").dropna().astype(int))
                clm_years = set(pd.to_numeric(clm["POLICY_YEAR"], errors="coerce").dropna().astype(int))
                usable = sorted(y for y in prem_years & clm_years if y <= complete)
                add("policies", "complete years with premium and claims", "PASS" if usable else "STOP", len(usable),
                    1, f"{usable[0]}-{usable[-1]}" if usable else "no policy year up to the cut-off has both premium and claims")

    out = pd.DataFrame(results, columns=["FILE", "CHECK", "STATUS", "VALUE", "LIMIT", "NOTE"])
    out["VALUE"], out["LIMIT"] = out["VALUE"].astype(str), out["LIMIT"].astype(str)
    S.facts_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(S.facts_dir / "fact_extract_validation.csv", index=False, encoding="utf-8")
    S.reports_dir.mkdir(parents=True, exist_ok=True)
    with open(S.reports_dir / "extract_validation_report.md", "w", encoding="utf-8") as f:
        f.write("# Extract validation\n\nThe extract checked against the data contract (`pipeline/contract.py`). "
                "STOP = fix the extract before running; WARN = the run goes on, with the consequence noted.\n\n"
                "| File | Check | Status | Value | Limit | Note |\n|---|---|---|---|---|---|\n")
        for r in out.itertuples():
            f.write(f"| {r.FILE} | {r.CHECK} | **{r.STATUS}** | {'' if r.VALUE == 'None' else r.VALUE} | "
                    f"{'' if r.LIMIT == 'None' else r.LIMIT} | {r.NOTE} |\n")

    counts = out["STATUS"].value_counts()
    _log(f"{len(out)} checks: {counts.get('PASS', 0)} pass, {counts.get('WARN', 0)} warn, {counts.get('STOP', 0)} stop")
    for r in out[out["STATUS"] != "PASS"].itertuples():
        _log(f"  {r.STATUS} [{r.FILE}] {r.CHECK}: {r.NOTE}")
    if counts.get("STOP", 0):
        _log("STOPPED: fix the extract (see extract_validation_report.md); nothing was built from it")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
