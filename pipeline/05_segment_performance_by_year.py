"""
05_segment_performance_by_year.py
---------------------------------
Builds fact_segment_performance_by_year.csv at the grain
    ZONE x CLASS_DESC x POLICY_TYPE_DESC x POLICY_YEAR
so Power BI can filter by policy year instead of summing every year.

Inputs (already produced by the pipeline, nothing is regenerated):
    02_facts/fact_earned_premium.csv
    02_facts/fact_policy_pricing.csv
    02_facts/fact_claim.csv
Output:
    02_facts/fact_segment_performance_by_year.csv

Rules are the same as the current fact_segment_performance:
    admin expense      = 10% of earned premium
    break-even premium = (claims x 1.05 risk margin + commission) / (1 - 10% admin)
    safe zone          = Green CR <= 90%, Yellow <= 100%, Red <= 110%, else Loss
    credibility        = Full >= 30 claims, Partial >= 10, else Low

Immature years: policy years after LATEST_MATURE_YEAR have too few reported
claims (2024: 41 claims on 1,779 policies). For those years the claims used are
the GLM expected claims from fact_policy_pricing, and CLAIMS_BASIS says so.
The actual figure is kept in ACTUAL_ULTIMATE_CLAIMS.
"""
from pathlib import Path
import sys
import numpy as np
import pandas as pd

# ---- settings -------------------------------------------------------------
FACTS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "SYNTHETIC_DSS_DATA" / "02_facts"
LATEST_MATURE_YEAR = 2023      # years after this use model-expected claims
ADMIN_RATE = 0.10
RISK_MARGIN = 0.05
FULL_CRED_CLAIMS, PARTIAL_CRED_CLAIMS = 30, 10
KEYS = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC", "POLICY_YEAR"]

# ---- load -----------------------------------------------------------------
ep = pd.read_csv(FACTS / "fact_earned_premium.csv",
                 usecols=KEYS + ["EARNED_PREMIUM"], encoding_errors="replace")
pp = pd.read_csv(FACTS / "fact_policy_pricing.csv",
                 usecols=KEYS + ["POLICY_NO", "EXPOSURE_YEARS", "COMMISSION",
                                 "pure_premium_period", "expected_frequency_period"],
                 encoding_errors="replace")
fc = pd.read_csv(FACTS / "fact_claim.csv",
                 usecols=["POLICY_NO", "POLICY_YEAR", "CLM_NO", "POLICY_MATCHED", "ULTIMATE_CLAIM"],
                 encoding_errors="replace")

# ---- aggregate per segment x policy year ----------------------------------
ep_g = ep.groupby(KEYS, as_index=False).EARNED_PREMIUM.sum()

pol_g = pp.groupby(KEYS, as_index=False).agg(
    POLICY_COUNT=("POLICY_NO", "size"),
    EXPOSURE_YEARS=("EXPOSURE_YEARS", "sum"),
    COMMISSION_SUM=("COMMISSION", "sum"),
    MODEL_EXPECTED_CLAIMS=("pure_premium_period", "sum"),
    MODEL_EXPECTED_CLAIM_COUNT=("expected_frequency_period", "sum"),
)

matched = fc[fc.POLICY_MATCHED.astype(str).str.lower() == "true"]
pol_keys = pp[["POLICY_NO", "POLICY_YEAR", "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]].drop_duplicates(["POLICY_NO", "POLICY_YEAR"])
clm = matched.merge(pol_keys, on=["POLICY_NO", "POLICY_YEAR"], how="inner")
clm_g = clm.groupby(KEYS, as_index=False).agg(
    ACTUAL_ULTIMATE_CLAIMS=("ULTIMATE_CLAIM", "sum"),
    ACTUAL_CLAIM_COUNT=("CLM_NO", "size"),
)

df = (ep_g.merge(pol_g, on=KEYS, how="outer")
          .merge(clm_g, on=KEYS, how="left"))
num = df.columns.difference(KEYS)
df[num] = df[num].fillna(0)

# ---- claims basis: actual for mature years, model-expected for immature ----
mature = df.POLICY_YEAR <= LATEST_MATURE_YEAR
df["CLAIMS_BASIS"] = np.where(mature, "Actual developed", "Model expected")
df["ULTIMATE_CLAIMS"] = np.where(mature, df.ACTUAL_ULTIMATE_CLAIMS, df.MODEL_EXPECTED_CLAIMS)
df["CLAIM_COUNT"] = np.where(mature, df.ACTUAL_CLAIM_COUNT, df.MODEL_EXPECTED_CLAIM_COUNT.round()).astype(int)

# ---- derived columns (same formulas as the current table) -----------------
P = df.EARNED_PREMIUM
div = lambda a, b: np.where(b != 0, a / b.replace(0, np.nan), np.nan)
df["ADMIN_EXPENSE"] = P * ADMIN_RATE
df["LOSS_RATIO"] = div(df.ULTIMATE_CLAIMS, P)
df["COMMISSION_RATIO"] = div(df.COMMISSION_SUM, P)
df["EXPENSE_RATIO"] = div(df.COMMISSION_SUM + df.ADMIN_EXPENSE, P)
df["COMBINED_RATIO"] = df.LOSS_RATIO + df.EXPENSE_RATIO
df["UW_PROFIT"] = P - df.ULTIMATE_CLAIMS - df.COMMISSION_SUM - df.ADMIN_EXPENSE
df["PURE_PREMIUM_PER_YEAR"] = div(df.ULTIMATE_CLAIMS, df.EXPOSURE_YEARS)
df["AVG_PREMIUM_PER_POLICY"] = div(P, df.POLICY_COUNT)
df["BREAK_EVEN_TOTAL"] = (df.ULTIMATE_CLAIMS * (1 + RISK_MARGIN) + df.COMMISSION_SUM) / (1 - ADMIN_RATE)
df["BREAK_EVEN_PER_EXPOSURE"] = div(df.BREAK_EVEN_TOTAL, df.EXPOSURE_YEARS)
df["MAX_SAFE_DISCOUNT"] = 1 - div(df.BREAK_EVEN_TOTAL, P)
cr = df.COMBINED_RATIO
df["SAFE_ZONE_STATUS"] = np.select([cr <= 0.90, cr <= 1.00, cr <= 1.10, cr > 1.10],
                                   ["Green", "Yellow", "Red", "Loss"], default="No premium")
df["CREDIBILITY_FLAG"] = np.select([df.CLAIM_COUNT >= FULL_CRED_CLAIMS, df.CLAIM_COUNT >= PARTIAL_CRED_CLAIMS],
                                   ["Full", "Partial"], default="Low")

cols = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC", "POLICY_YEAR",
        "EARNED_PREMIUM", "ULTIMATE_CLAIMS", "CLAIM_COUNT", "EXPOSURE_YEARS", "POLICY_COUNT",
        "COMMISSION_SUM", "LOSS_RATIO", "COMMISSION_RATIO", "ADMIN_EXPENSE", "EXPENSE_RATIO",
        "COMBINED_RATIO", "UW_PROFIT", "PURE_PREMIUM_PER_YEAR", "AVG_PREMIUM_PER_POLICY",
        "BREAK_EVEN_TOTAL", "BREAK_EVEN_PER_EXPOSURE", "MAX_SAFE_DISCOUNT",
        "SAFE_ZONE_STATUS", "CREDIBILITY_FLAG",
        "CLAIMS_BASIS", "ACTUAL_ULTIMATE_CLAIMS", "ACTUAL_CLAIM_COUNT",
        "MODEL_EXPECTED_CLAIMS", "MODEL_EXPECTED_CLAIM_COUNT"]
df = df[cols].sort_values(KEYS).reset_index(drop=True)

# ---- reconciliation: all years together must equal the old table ----------
old_path = FACTS / "fact_segment_performance.csv"
if old_path.exists():
    old = pd.read_csv(old_path, encoding_errors="replace")
    checks = {
        "EARNED_PREMIUM": (df.EARNED_PREMIUM.sum(), old.EARNED_PREMIUM.sum()),
        "ACTUAL claims (all years)": (df.ACTUAL_ULTIMATE_CLAIMS.sum(), old.ULTIMATE_CLAIMS.sum()),
        "ACTUAL claim count": (df.ACTUAL_CLAIM_COUNT.sum(), old.CLAIM_COUNT.sum()),
        "EXPOSURE_YEARS": (df.EXPOSURE_YEARS.sum(), old.EXPOSURE_YEARS.sum()),
        "POLICY_COUNT": (df.POLICY_COUNT.sum(), old.POLICY_COUNT.sum()),
        "COMMISSION_SUM": (df.COMMISSION_SUM.sum(), old.COMMISSION_SUM.sum()),
    }
    print("Reconciliation vs current fact_segment_performance (all years):")
    for k, (new, prev) in checks.items():
        ok = abs(new - prev) <= max(1.0, abs(prev) * 1e-9)
        print(f"  {'OK ' if ok else 'DIFF'}  {k:28s} new={new:,.2f}  old={prev:,.2f}")

out = FACTS / "fact_segment_performance_by_year.csv"
df.to_csv(out, index=False, encoding="utf-8")
print(f"\nWrote {len(df):,} rows -> {out}")
print(df.groupby(["POLICY_YEAR", "CLAIMS_BASIS"])[["EARNED_PREMIUM", "ULTIMATE_CLAIMS"]].sum()
        .assign(LOSS_RATIO=lambda t: t.ULTIMATE_CLAIMS / t.EARNED_PREMIUM).round(3).to_string())