"""
01_prepare_real.py — turn the warehouse extract into the tables the models and Power BI use.

Reads : <raw_dir>/policies.parquet, <raw_dir>/claims.parquet   (from 00_extract_oracle.py)
Writes: <output_root>/02_facts/*.csv, <output_root>/03_dims/*.csv,
        <output_root>/04_validation/prepare_report.md

This is the real-data counterpart of 01_build_synthetic_data.py. It does NOT invent anything:
no claim reassignment, no synthetic development, no random sum insured. The shared business
logic (earned premium, segment performance, break-even, safe zone, triangles, scenario grid)
is reused from 01_build_synthetic_data.py, so both sources follow the same rules.

Every output table keeps the exact columns and column order of the synthetic tables, so the
Power BI queries work unchanged; columns the warehouse does not provide are left empty and
listed in the report.

ASSUMPTIONS (see config.toml [prepare] and [business]):
  - ultimate claim = incurred (paid + case reserve), no IBNR, unless configured otherwise
  - exposure = days in force / 365.25, capped between 0.01 and 2 years
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from contract import CLAIMS, POLICIES
from settings import ROOT, S, is_motor
from zone_mapping import map_office_to_zone

TEMPLATE_DIR = ROOT / "SYNTHETIC_DSS_DATA"      # column layout Power BI expects
report: list[str] = []


def log(msg: str) -> None:
    print(f"[prepare] {msg}", flush=True)
    report.append(msg)


def load_builder():
    """Import the shared business logic from 01_build_synthetic_data.py."""
    spec = importlib.util.spec_from_file_location(
        "synthetic_builder", Path(__file__).with_name("01_build_synthetic_data.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_contract(df: pd.DataFrame, contract: dict, name: str) -> pd.DataFrame:
    df = df.rename(columns=str.upper)
    missing = [c for c in contract["required"] if c not in df.columns]
    if missing:
        sys.exit(f"{name}: required columns missing from the extract: {', '.join(missing)}.\n"
                 f"Fix the column aliases in sql/{name}.sql (see pipeline/contract.py).")
    absent = [c for c in contract["recommended"] if c not in df.columns]
    if absent:
        log(f"{name}: recommended columns not provided (left empty/defaulted): {', '.join(absent)}")
    return df


def conform(df: pd.DataFrame, table: str, folder: str = "02_facts") -> pd.DataFrame:
    """Give the table exactly the columns (and order) of the synthetic version."""
    template = TEMPLATE_DIR / folder / f"{table}.csv"
    if not template.exists():
        return df
    cols = pd.read_csv(template, nrows=0).columns.tolist()
    filled = [c for c in cols if c not in df.columns]
    if filled:
        log(f"{table}: columns not available from the warehouse, left empty: {', '.join(filled)}")
    for c in filled:
        df[c] = np.nan
    return df[cols]


# ---------------------------------------------------------------------------- policies
def prepare_policies(raw: pd.DataFrame) -> pd.DataFrame:
    p = check_contract(raw, POLICIES, "policies")
    for c in ("POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE"):
        p[c] = pd.to_datetime(p[c], errors="coerce")
    bad = p["POLICY_EFFECTIVE_DATE"].isna() | p["POLICY_EXPIRY_DATE"].isna()
    if bad.any():
        log(f"policies: {int(bad.sum()):,} rows dropped (missing cover dates)")
        p = p[~bad].copy()
    for c in ("TOTAL_PREMIUM", "COMMISSION", "SUM_INSURED", "GROSS_PREMIUM", "NET_PREMIUM"):
        if c in p:
            p[c] = pd.to_numeric(p[c], errors="coerce")
    p["COMMISSION"] = p["COMMISSION"].fillna(0)
    p["EXPOSURE_DAYS"] = (p["POLICY_EXPIRY_DATE"] - p["POLICY_EFFECTIVE_DATE"]).dt.days.clip(lower=1)
    p["EXPOSURE_YEARS"] = (p["EXPOSURE_DAYS"] / 365.25).clip(lower=0.01, upper=2.0)

    # geography: OFFICE_NAME -> CITY/REGION/ZONE (same mapping as the synthetic build)
    # A ZONE supplied by the warehouse wins; the mapping only fills the gaps.
    if "OFFICE_NAME" in p:
        lookup = pd.DataFrame([map_office_to_zone(o) for o in p["OFFICE_NAME"].dropna().unique()])
        if not lookup.empty:
            if "ZONE" in p:
                lookup = lookup.rename(columns={"ZONE": "ZONE_map"})
            p = p.drop(columns=["CITY", "REGION"], errors="ignore").merge(lookup, on="OFFICE_NAME", how="left")
            if "ZONE_map" in p:
                p["ZONE"] = p["ZONE"].fillna(p.pop("ZONE_map"))
    for c, default in (("ZONE", "Unspecified"), ("CITY", "Unknown"), ("REGION", "Unknown")):
        if c not in p:
            p[c] = np.nan
        p[c] = p[c].fillna(default)
    if "SOURCE_DESC" not in p:
        p["SOURCE_DESC"] = "Unknown"

    if "SUM_INSURED" in p:
        bins = [0, 25000, 50000, 100000, 200000, 500000, np.inf]
        labels = ["<25k", "25-50k", "50-100k", "100-200k", "200-500k", ">500k"]
        p["SUM_INSURED_BAND"] = pd.cut(p["SUM_INSURED"], bins=bins, labels=labels).astype(object)
    for code, desc in (("CLASS_NO", "CLASS_DESC"), ("POLICY_TYPE_NO", "POLICY_TYPE_DESC")):
        if code not in p:      # stable codes when the warehouse has none
            p[code] = p[desc].map({d: i + 1 for i, d in enumerate(sorted(p[desc].dropna().unique()))})
    for c in ("GROSS_PREMIUM", "NET_PREMIUM"):
        if c not in p:
            p[c] = p["TOTAL_PREMIUM"]
    p["POLICY_YEAR_KEY"] = p["POLICY_NO"].astype(str) + "_" + p["POLICY_YEAR"].astype(str)
    p["IS_MOTOR"] = is_motor(p["CLASS_DESC"])
    p["IS_CANCELLED"] = p["TOTAL_PREMIUM"] < 0

    dups = p.duplicated(["POLICY_NO", "POLICY_YEAR"]).sum()
    if dups:
        log(f"policies: WARNING {int(dups):,} duplicate POLICY_NO+POLICY_YEAR rows (check the SQL)")
    log(f"policies: {len(p):,} rows, {p['POLICY_YEAR'].min()}-{p['POLICY_YEAR'].max()}, "
        f"classes: {', '.join(sorted(p['CLASS_DESC'].astype(str).unique()))}")
    return p


# ---------------------------------------------------------------------------- keys
def normalize_keys(p: pd.DataFrame, c: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Make POLICY_NO / POLICY_YEAR the same type on both sides so claims match policies.
    Warehouses often return 2015 on one table and 2015.0 (or '2015') on the other. Model steps
    need integer keys: non-numeric policy numbers are replaced by stable surrogate integers."""
    for col in ("POLICY_YEAR", "POLICY_NO"):
        pn, cn = pd.to_numeric(p[col], errors="coerce"), pd.to_numeric(c[col], errors="coerce")
        numeric = (pn.notna().all() and (cn.notna() | c[col].isna()).all()
                   and (pn % 1 == 0).all() and (cn.dropna() % 1 == 0).all())
        if numeric:
            p[col], c[col] = pn.astype("int64"), cn.astype("Int64")
        elif col == "POLICY_YEAR":
            sys.exit("POLICY_YEAR must be a year number in both extracts.")
        else:
            ps, cs = p[col].astype(str).str.strip(), c[col].astype("string").str.strip()
            codes = {k: i + 1 for i, k in enumerate(sorted(set(ps) | set(cs.dropna())))}
            p[col], c[col] = ps.map(codes).astype("int64"), cs.map(codes).astype("Int64")
            log("POLICY_NO is not numeric: replaced by surrogate integer keys in the outputs "
                "(the original numbers stay in the warehouse/extract)")
    return p, c


# ---------------------------------------------------------------------------- claims
def prepare_claims(raw: pd.DataFrame, policies: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """raw has already passed check_contract and normalize_keys."""
    c = raw.copy()
    for col in ("ACC_DATE", "CLOSE_DATE", "REPORT_DATE"):
        if col in c:
            c[col] = pd.to_datetime(c[col], errors="coerce")
    for col in ("TOTAL_PAID", "CASE_RESERVE", "TOTAL_EST", "RECOVERY_AMT"):
        if col in c:
            c[col] = pd.to_numeric(c[col], errors="coerce")
    c["TOTAL_PAID"] = c["TOTAL_PAID"].fillna(0)
    if "CLM_YEAR" not in c:
        c["CLM_YEAR"] = c["ACC_DATE"].dt.year

    keys = set(zip(policies["POLICY_NO"].astype(str), policies["POLICY_YEAR"].astype(str)))
    c["POLICY_MATCHED"] = [k in keys for k in zip(c["POLICY_NO"].astype(str), c["POLICY_YEAR"].astype(str))]
    rate = c["POLICY_MATCHED"].mean() if len(c) else 0
    log(f"claims: {len(c):,} rows, {rate:.1%} matched to a policy-year"
        + ("  <-- LOW: check POLICY_NO/POLICY_YEAR in both SQL files" if rate < 0.9 else ""))
    if rate == 0:
        sys.exit("No claim matches a policy-year: the models cannot run. Check that claims.sql "
                 "and policies.sql return the same POLICY_NO and POLICY_YEAR values.")

    # case reserve, incurred, closed flag
    if "CASE_RESERVE" in c:
        c["CASE_RESERVE"] = c["CASE_RESERVE"].fillna(0).clip(lower=0)
    elif "TOTAL_EST" in c:
        c["CASE_RESERVE"] = (c["TOTAL_EST"] - c["TOTAL_PAID"]).clip(lower=0).fillna(0)
        log("claims: CASE_RESERVE derived as TOTAL_EST - TOTAL_PAID")
    else:
        c["CASE_RESERVE"] = 0.0
        log("claims: no reserve information; CASE_RESERVE = 0  [check with the claims team]")
    c["INCURRED"] = c["TOTAL_PAID"] + c["CASE_RESERVE"]
    if "IS_CLOSED" in c:
        c["IS_CLOSED"] = c["IS_CLOSED"].astype(str).str.upper().isin(["1", "Y", "YES", "TRUE", "C", "CLOSED"])
    elif "CLOSE_DATE" in c:
        c["IS_CLOSED"] = c["CLOSE_DATE"].notna()
    else:
        c["IS_CLOSED"] = False
    if "CLOSE_DATE" not in c:
        c["CLOSE_DATE"] = pd.NaT
    if "REPORT_DATE" not in c:
        c["REPORT_DATE"] = pd.NaT

    method = S.config.get("prepare", {}).get("ultimate_method", "incurred")
    if method != "incurred":
        sys.exit(f"ultimate_method '{method}' is not implemented yet (only 'incurred').")
    c["ULTIMATE_CLAIM"] = c["INCURRED"]
    c["IBNR_COMPONENT"] = 0.0
    log("claims: ULTIMATE_CLAIM = incurred (paid + case reserve), no IBNR  [ASSUMPTION]")

    end = c["CLOSE_DATE"].where(c["IS_CLOSED"] & c["CLOSE_DATE"].notna(), as_of)
    c["DEV_MONTHS"] = ((end - c["ACC_DATE"]).dt.days / 30.44).round().astype("Int64")

    # attributes the dashboard reads from fact_claim, taken from the matched policy
    attrs = ["POLICY_NO", "POLICY_YEAR"] + [x for x in (
        "BRANCH_NO", "BRANCH_NAME", "OFFICE_NO", "OFFICE_NAME", "DEPARTMENT_NO", "DEPARTMENT_NAME",
        "POLICY_TYPE_NO", "POLICY_TYPE_DESC", "SOURCE_NO", "SOURCE_DESC", "CLIENT_NO", "CLIENT_NAME",
        "CLASS_NO", "CLASS_DESC") if x in policies]
    pol = policies[attrs].drop_duplicates(["POLICY_NO", "POLICY_YEAR"]).copy()
    pol["_k"] = pol["POLICY_NO"].astype(str) + "|" + pol["POLICY_YEAR"].astype(str)
    c["_k"] = c["POLICY_NO"].astype(str) + "|" + c["POLICY_YEAR"].astype(str)
    c = c.merge(pol.drop(columns=["POLICY_NO", "POLICY_YEAR"]), on="_k", how="left", suffixes=("", "_pol"))
    for col in [x for x in c.columns if x.endswith("_pol")]:
        base = col[:-4]
        c[base] = c[base].fillna(c.pop(col)) if base in c else c.pop(col)
    c = c.drop(columns="_k")
    c["COB"] = c.get("CLASS_NO")
    c["COB_DESC"] = c.get("CLASS_DESC")
    c["POLICY_TYPE"] = c.get("POLICY_TYPE_NO")
    c["ORIGINAL_POLICY_NO"] = c["POLICY_NO"]
    return c


# ---------------------------------------------------------------------------- dims
def build_dims(p: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, pd.DataFrame]:
    if "OFFICE_NAME" not in p:
        p = p.assign(OFFICE_NAME=p["ZONE"])
    start = p["POLICY_EFFECTIVE_DATE"].min().normalize().replace(month=1, day=1)
    end = max(p["POLICY_EXPIRY_DATE"].max(), as_of).normalize()
    dates = pd.date_range(start, end, freq="D")
    return {
        "dim_zone": p[["OFFICE_NAME", "CITY", "REGION", "ZONE"]].drop_duplicates(),
        "dim_business_class": p[["CLASS_NO", "CLASS_DESC"]].drop_duplicates(),
        "dim_product": p[["POLICY_TYPE_NO", "POLICY_TYPE_DESC"]].drop_duplicates(),
        "dim_date": pd.DataFrame({
            "DATE": dates, "YEAR": dates.year, "QUARTER": dates.quarter, "MONTH": dates.month,
            "MONTH_NAME": dates.strftime("%b"), "YEAR_MONTH": dates.strftime("%Y-%m"),
            "DAY_OF_WEEK": dates.day_name()}),
    }


# ---------------------------------------------------------------------------- main
def main() -> None:
    if S.raw_dir is None:
        sys.exit("The active source has no raw_dir; run with --source oracle.")
    raw_pol, raw_clm = S.raw_dir / "policies.parquet", S.raw_dir / "claims.parquet"
    for f in (raw_pol, raw_clm):
        if not f.exists():
            sys.exit(f"Missing {f}. Run the extract first: python pipeline/00_extract_oracle.py")

    as_of_cfg = S.config.get("prepare", {}).get("as_of_date") or ""
    as_of = pd.Timestamp(as_of_cfg) if as_of_cfg else pd.Timestamp(
        date.fromtimestamp(raw_clm.stat().st_mtime))
    log(f"source=oracle  as_of={as_of.date()}  output={S.output_root}")

    B = load_builder()
    biz = S.config.get("business", {})
    B.ASSUMPTIONS["ADMIN_EXPENSE_RATE"] = biz.get("admin_expense_rate", B.ASSUMPTIONS["ADMIN_EXPENSE_RATE"])
    B.ASSUMPTIONS["RISK_MARGIN_RATE"] = biz.get("risk_margin_rate", B.ASSUMPTIONS["RISK_MARGIN_RATE"])

    policies = prepare_policies(pd.read_parquet(raw_pol))
    B.ASSUMPTIONS["MOTOR_CLASSES"] = S.motor_classes or sorted(policies["CLASS_DESC"].dropna().unique())
    policies, raw_claims = normalize_keys(policies, check_contract(pd.read_parquet(raw_clm), CLAIMS, "claims"))
    claims = prepare_claims(raw_claims, policies, as_of)

    ep = B.build_earned_premium(policies)
    triangles = B.build_triangles(claims)
    segments = B.build_segment_performance(ep, claims, policies)
    scenarios = B.build_scenario_table(segments)
    surface = B.build_profitability_surface(segments)
    claims_dev = B.build_fact_claims_dev(claims, triangles)

    facts = {
        "fact_policy": policies, "fact_claim": claims, "fact_earned_premium": ep,
        "fact_segment_performance": segments, "fact_profitability": segments,
        "fact_safe_zone": segments, "fact_scenarios": scenarios,
        "fact_profitability_surface": surface, "fact_claims_development": claims_dev,
        "fact_triangle_paid": triangles["triangle_paid"],
        "fact_triangle_incurred": triangles["triangle_incurred"],
        "fact_dev_factors_paid": triangles["dev_factors_paid"],
        "fact_ultimate_by_ay": triangles["ultimate_by_ay"],
    }
    for name, df in facts.items():
        conform(df.copy(), name).to_csv(S.facts_dir / f"{name}.csv", index=False, encoding="utf-8")
    for name, df in build_dims(policies, as_of).items():
        conform(df.copy(), name, "03_dims").to_csv(S.dims_dir / f"{name}.csv", index=False, encoding="utf-8")

    tot_ep, tot_ult = ep["EARNED_PREMIUM"].sum(), claims.loc[claims["POLICY_MATCHED"], "ULTIMATE_CLAIM"].sum()
    log(f"earned premium SAR {tot_ep:,.0f}; ultimate claims (matched) SAR {tot_ult:,.0f}; "
        f"loss ratio {tot_ult / tot_ep:.1%}; segments {len(segments)}" if tot_ep else "no earned premium")

    val = S.output_root / "04_validation"
    val.mkdir(parents=True, exist_ok=True)
    (val / "prepare_report.md").write_text(
        "# Prepare report (real data)\n\n" + "\n".join(f"- {m}" for m in report) + "\n", encoding="utf-8")
    log(f"report: {val / 'prepare_report.md'}")


if __name__ == "__main__":
    main()
