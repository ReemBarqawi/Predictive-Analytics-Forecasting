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
from pathlib import Path

import numpy as np
import pandas as pd

import exposure
from contract import CLAIMS, POLICIES
from settings import ROOT, S, is_motor, valuation_date
from zone_mapping import map_office_to_zone, with_office_location

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


# Columns the real data adds beyond the synthetic template, kept after the template's columns
EXTRA_COLUMNS = {"fact_policy": ["VEHICLE_AGE_BAND", "DRIVER_AGE_BAND", "CLASS_OF_USE", "PRIOR_CLAIMS",
                                 "CARS", "CAR_YEARS", "CARS_SOURCE"]}   # plan H1 (pipeline/exposure.py)


def conform(df: pd.DataFrame, table: str, folder: str = "02_facts") -> pd.DataFrame:
    """Give the table the columns (and order) of the synthetic version, then any EXTRA_COLUMNS."""
    template = TEMPLATE_DIR / folder / f"{table}.csv"
    if not template.exists():
        return df
    cols = pd.read_csv(template, nrows=0).columns.tolist()
    filled = [c for c in cols if c not in df.columns]
    if filled:
        log(f"{table}: columns not available from the warehouse, left empty: {', '.join(filled)}")
    for c in filled:
        df[c] = np.nan
    return df[cols + [c for c in EXTRA_COLUMNS.get(table, []) if c in df.columns]]


# ---------------------------------------------------------------------------- policies
def prepare_policies(raw: pd.DataFrame) -> pd.DataFrame:
    p = check_contract(raw, POLICIES, "policies")
    for c in ("POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE"):
        p[c] = pd.to_datetime(p[c], errors="coerce")
    bad = p["POLICY_EFFECTIVE_DATE"].isna() | p["POLICY_EXPIRY_DATE"].isna()
    if bad.any():
        log(f"policies: {int(bad.sum()):,} rows dropped (missing cover dates)")
        p = p[~bad].copy()
    # Typing errors in old records (years 9, 209, 1429, expiry 3011): the start must fall within a
    # year of the policy's own POLICY_YEAR, and cover must end after it starts and within two years
    # (the same limit as EXPOSURE_YEARS below).
    eff_year = p["POLICY_EFFECTIVE_DATE"].dt.year
    year = pd.to_numeric(p["POLICY_YEAR"], errors="coerce")
    term = (p["POLICY_EXPIRY_DATE"] - p["POLICY_EFFECTIVE_DATE"]).dt.days
    impossible = ~eff_year.between(year - 1, year + 1) | (term <= 0) | (term > 2 * 366)
    if impossible.any():
        log(f"policies: {int(impossible.sum()):,} rows dropped (impossible cover dates, "
            f"premium SAR {p.loc[impossible, 'TOTAL_PREMIUM'].astype(float).sum():,.0f})")
        p = p[~impossible].copy()
    for c in ("POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE"):
        p[c] = p[c].astype("datetime64[ns]")
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
        lookup = lookup[["OFFICE_NAME", "CITY", "REGION", "ZONE"]] if not lookup.empty else lookup  # map columns go on dim_zone
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


# ---------------------------------------------------------------------------- vehicles
def prepare_vehicles(raw: pd.DataFrame, policies: pd.DataFrame, key_map: dict) -> pd.DataFrame:
    """One row per vehicle x policy-year (sql/vehicles.sql), keyed like fact_policy. VEHICLE_ID is a
    one-way hash of the chassis number: it links a car to its next policy (step 17). Ages outside a
    plausible range are typing errors and left empty."""
    v = raw.copy()
    for c in ("COVER_START", "COVER_END", "STATUS_DATE"):
        v[c] = pd.to_datetime(v[c], errors="coerce")
    v["POLICY_YEAR"] = pd.to_numeric(v["POLICY_YEAR"], errors="coerce").astype("Int64")
    v["POLICY_NO"] = v["POLICY_NO"].astype(str).str.strip().map(key_map).astype("Int64")
    keys = policies[["POLICY_NO", "POLICY_YEAR"]].drop_duplicates()
    v = v.merge(keys.assign(_P=1), on=["POLICY_NO", "POLICY_YEAR"], how="left")
    matched = v.pop("_P").notna()
    log(f"vehicles: {len(v):,} rows, {matched.mean():.1%} matched to a policy-year"
        + ("  <-- LOW: check the policy key in sql/vehicles.sql" if matched.mean() < 0.9 else ""))
    v = v[matched & v["COVER_START"].notna() & v["COVER_END"].notna()].copy()
    v["POLICY_NO"] = v["POLICY_NO"].astype("int64")
    v["POLICY_YEAR"] = v["POLICY_YEAR"].astype("int64")
    for c in ("GROSS_PREMIUM", "SUM_INSURED", "YEAR_OF_MAKE", "DRIVER_AGE"):
        v[c] = pd.to_numeric(v[c], errors="coerce")
    v["DRIVER_AGE"] = v["DRIVER_AGE"].where(v["DRIVER_AGE"].between(16, 100))
    v["VEHICLE_AGE"] = (v["COVER_START"].dt.year - v["YEAR_OF_MAKE"]).where(lambda a: a.between(-1, 60))
    return v


# Method constants: the bands of the vehicle rating factors (plan D1), standard motor bands.
VEHICLE_AGE_BANDS = ([-1, 2, 5, 10, 15, 100], ["0-2", "3-5", "6-10", "11-15", "16+"])
DRIVER_AGE_BANDS = ([15, 24, 34, 44, 54, 64, 120], ["<25", "25-34", "35-44", "45-54", "55-64", "65+"])
# A car's previous policy counts for prior claims when it ended at most this long before the next began
PRIOR_POLICY_MAX_GAP_DAYS = 400


def vehicle_factors(v: pd.DataFrame, claims: pd.DataFrame) -> pd.DataFrame:
    """Policy-level rating factors from the vehicles (plan D1-D2), one row per POLICY_NO x POLICY_YEAR:
    VEHICLE_AGE_BAND (mean vehicle age), DRIVER_AGE_BAND (median driver age), CLASS_OF_USE (most common)
    and PRIOR_CLAIMS: the claims on the car's previous policy with the company ("new to us" when it
    had none; a fleet's previous claims are shared over its cars)."""
    v = v.sort_values(["VEHICLE_ID", "COVER_START", "POLICY_YEAR"]).copy()
    per_pol = claims[claims["POLICY_MATCHED"]].groupby(["POLICY_NO", "POLICY_YEAR"]).size().rename("N_CLAIMS")
    cars = v.groupby(["POLICY_NO", "POLICY_YEAR"]).size().rename("N_CARS")
    rate = (per_pol / cars).reindex(cars.index).fillna(0.0).rename("CLAIMS_PER_CAR").reset_index()
    prev = v.groupby("VEHICLE_ID")[["POLICY_NO", "POLICY_YEAR", "COVER_END"]].shift(1)
    linked = (v["COVER_START"] - prev["COVER_END"]).dt.days.between(-366, PRIOR_POLICY_MAX_GAP_DAYS)
    prev = prev.assign(POLICY_NO=prev["POLICY_NO"].astype("Int64"), POLICY_YEAR=prev["POLICY_YEAR"].astype("Int64"))
    prior = prev.merge(rate.astype({"POLICY_NO": "Int64", "POLICY_YEAR": "Int64"}), on=["POLICY_NO", "POLICY_YEAR"],
                       how="left")["CLAIMS_PER_CAR"].to_numpy()
    v["PRIOR"] = np.where(linked.to_numpy(), np.nan_to_num(prior, nan=0.0), np.nan)

    g = v.groupby(["POLICY_NO", "POLICY_YEAR"])
    out = pd.DataFrame({
        "VEHICLE_AGE": g["VEHICLE_AGE"].mean(), "DRIVER_AGE": g["DRIVER_AGE"].median(),
        "CLASS_OF_USE": g["CLASS_OF_USE"].agg(lambda s: s.mode().iloc[0] if s.notna().any() else np.nan),
        "PRIOR_SUM": g["PRIOR"].sum(min_count=1)}).reset_index()
    out["VEHICLE_AGE_BAND"] = pd.cut(out["VEHICLE_AGE"], bins=VEHICLE_AGE_BANDS[0],
                                     labels=VEHICLE_AGE_BANDS[1]).astype(object).fillna("Unknown")
    out["DRIVER_AGE_BAND"] = pd.cut(out["DRIVER_AGE"], bins=DRIVER_AGE_BANDS[0],
                                    labels=DRIVER_AGE_BANDS[1]).astype(object).fillna("Unknown")
    out["CLASS_OF_USE"] = out["CLASS_OF_USE"].map(lambda x: "Unknown" if pd.isna(x) else f"use {int(x)}")
    out["PRIOR_CLAIMS"] = np.select([out["PRIOR_SUM"].isna(), out["PRIOR_SUM"] < 0.5], ["new to us", "0"], "1+")
    return out[["POLICY_NO", "POLICY_YEAR", "VEHICLE_AGE_BAND", "DRIVER_AGE_BAND", "CLASS_OF_USE", "PRIOR_CLAIMS"]]


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
        "dim_zone": with_office_location(p[["OFFICE_NAME", "CITY", "REGION", "ZONE"]].drop_duplicates()),
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

    as_of = pd.Timestamp(valuation_date())
    log(f"source=oracle  as_of={as_of.date()}  output={S.output_root}")

    B = load_builder()
    biz = S.config.get("business", {})
    B.ASSUMPTIONS["ADMIN_EXPENSE_RATE"] = biz.get("admin_expense_rate", B.ASSUMPTIONS["ADMIN_EXPENSE_RATE"])
    B.ASSUMPTIONS["RISK_MARGIN_RATE"] = biz.get("risk_margin_rate", B.ASSUMPTIONS["RISK_MARGIN_RATE"])

    policies = prepare_policies(pd.read_parquet(raw_pol))
    B.ASSUMPTIONS["MOTOR_CLASSES"] = S.motor_classes or sorted(policies["CLASS_DESC"].dropna().unique())
    original_keys = policies["POLICY_NO"].astype(str).str.strip().to_numpy()
    policies, raw_claims = normalize_keys(policies, check_contract(pd.read_parquet(raw_clm), CLAIMS, "claims"))
    claims = prepare_claims(raw_claims, policies, as_of)
    raw_veh = S.raw_dir / "vehicles.parquet"
    vehicles = None
    if raw_veh.exists():      # optional: renewal rates (step 17) and vehicle rating factors need it
        key_map = dict(zip(original_keys, policies["POLICY_NO"].to_numpy()))
        vehicles = prepare_vehicles(pd.read_parquet(raw_veh), policies, key_map)
        vehicles.to_parquet(S.facts_dir / "fact_vehicle.parquet", index=False)
        # plan D1-D2: vehicle rating factors, added after the existing fact_policy columns
        policies = policies.merge(vehicle_factors(vehicles, claims), on=["POLICY_NO", "POLICY_YEAR"], how="left")
        for c in ("VEHICLE_AGE_BAND", "DRIVER_AGE_BAND", "CLASS_OF_USE"):
            policies[c] = policies[c].fillna("Unknown")
        # No vehicle history known = no prior claims known: "new to us", not a separate "Unknown" that
        # would nearly duplicate the other factors' Unknown and destabilise the GLM
        policies["PRIOR_CLAIMS"] = policies["PRIOR_CLAIMS"].fillna("new to us")
        known = (policies["VEHICLE_AGE_BAND"] != "Unknown").mean()
        log(f"vehicle factors: {known:.1%} of policies have vehicle data; prior claims "
            + ", ".join(f"{k} {v:.1%}" for k, v in policies["PRIOR_CLAIMS"].value_counts(normalize=True).items()))
    else:
        log("vehicles: no extract (sql/vehicles.sql) - renewal rates fall back to config.toml")
    # Plan H1: cars per policy and car-years, the GLMs' exposure unit
    policies["CARS"], policies["CARS_SOURCE"] = exposure.car_counts(policies, vehicles)
    policies["CAR_YEARS"] = policies["CARS"] * policies["EXPOSURE_YEARS"]
    by_source = policies.groupby("CARS_SOURCE")["CAR_YEARS"].sum() / policies["CAR_YEARS"].sum()
    log("cars: " + ", ".join(f"{c} {policies.loc[policies['CLASS_DESC'] == c, 'CARS'].mean():.1f}"
                             for c in sorted(policies["CLASS_DESC"].dropna().unique()))
        + " average per policy; car-years from " + ", ".join(f"{k} {v:.1%}" for k, v in by_source.items()))

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
