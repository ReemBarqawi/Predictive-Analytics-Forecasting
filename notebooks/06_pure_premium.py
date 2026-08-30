"""
notebooks/06_pure_premium.py
=============================
Phase 2 of the post-data plan (Day 3).

Loads the fitted GLMs from Phase 1 and computes per-policy pure premium
    pure_premium_i = E[N_i] × E[S_i]
for every motor policy. Then compares each policy's pure premium against
its charged premium to identify segments that are underpriced, adequately
priced, or overpriced.

The key thing this file does that the OLD 03_glm_models.py did NOT:
    it produces a DIFFERENT prediction for every policy.
The old code used the mean predicted severity for all policies, which
collapses all segment differences into the frequency model. This is fixed here.

Outputs
-------
  SYNTHETIC_DSS_DATA/02_facts/fact_policy_pricing.csv
      One row per motor policy-year with expected frequency, expected severity,
      pure premium, charged premium, and pricing adequacy ratio.
  outputs/pricing_adequacy_by_segment.csv
      Aggregated adequacy by (ZONE, CLASS_DESC, POLICY_TYPE_DESC).
  outputs/pure_premium_report.md
      One-page summary: portfolio adequacy, top under- and over-priced segments,
      credibility caveats.

Prerequisite: run notebooks/05_glm_refit.py first (produces the two .pkl files).
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

# =========================================================================
# Config
# =========================================================================
BASE = Path(__file__).resolve().parent.parent
FACT_DIR = BASE / "SYNTHETIC_DSS_DATA" / "02_facts"
MODELS_DIR = BASE / "models"
OUTPUTS_DIR = BASE / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

MOTOR_CLASSES = ["Motor Private", "Motor Commercial", "Motor Rent a Car"]

# Segment definition for the adequacy roll-up
SEGMENT_KEYS = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]

# Credibility thresholds (based on policy count in the segment)
FULL_CREDIBILITY_MIN_POLICIES = 300
PARTIAL_CREDIBILITY_MIN_POLICIES = 50


def _log(msg: str) -> None:
    print(f"[pure_premium] {msg}", flush=True)


# =========================================================================
# Encoder — the TRANSFORM half of Phase 1's encode_features
# (Duplicated here so the file is self-contained. When you add Phase 3,
# consider extracting this + Phase 1's version into notebooks/_utils.py.)
# =========================================================================
def transform_features(df: pd.DataFrame, encoder_meta: dict) -> pd.DataFrame:
    """
    Apply saved rare-category rules and one-hot encoding, aligned to the
    training column set exactly.
    """
    feature_cols = list(encoder_meta["category_maps"].keys())
    df2 = df.copy()

    for col in feature_cols:
        mapping = encoder_meta["category_maps"][col]
        kept = set(mapping["kept"])
        def _apply(x, kept=kept):
            if pd.isna(x):
                return "Unknown"
            return x if x in kept else "Other"
        df2[col] = df2[col].astype(str).replace({"nan": "Unknown"}).apply(_apply)

    X = pd.get_dummies(df2[feature_cols], drop_first=True, dtype=int)

    # Align: add any missing training columns as zero; drop any extras; keep order
    for c in encoder_meta["columns"]:
        if c not in X.columns:
            X[c] = 0
    X = X[encoder_meta["columns"]]

    return X


# =========================================================================
# Load models
# =========================================================================
def load_models() -> tuple[dict, dict]:
    _log("Loading fitted GLMs from Phase 1")
    with open(MODELS_DIR / "frequency_glm.pkl", "rb") as f:
        freq = pickle.load(f)
    with open(MODELS_DIR / "severity_glm.pkl", "rb") as f:
        sev = pickle.load(f)
    _log(f"  frequency: {freq['family']} with {freq['link']} link, offset={freq.get('offset','none')}")
    _log(f"  severity:  {sev['family']} with {sev['link']} link, target={sev['target']}")
    return freq, sev


# =========================================================================
# Load and prepare policies
# =========================================================================
def load_policies() -> pd.DataFrame:
    _log("Loading motor policies from fact_policy.csv")
    p = pd.read_csv(FACT_DIR / "fact_policy.csv", low_memory=False)
    p = p[p["CLASS_DESC"].isin(MOTOR_CLASSES)].copy()
    p = p[p["EXPOSURE_YEARS"] > 0].copy()

    # Count how many policies have missing SUM_INSURED_BAND for the report
    n_missing_si = int(p["SUM_INSURED_BAND"].isna().sum())
    _log(f"  motor policies with positive exposure: {len(p):,}")
    _log(f"  policies with missing SUM_INSURED_BAND: {n_missing_si:,} "
         f"({n_missing_si/len(p):.1%}) → will be scored as SUM_INSURED_BAND='Unknown'")
    return p


# =========================================================================
# Predict per-policy frequency, severity, and pure premium
# =========================================================================
def score_policies(policies: pd.DataFrame, freq_bundle: dict, sev_bundle: dict) -> pd.DataFrame:
    """
    Returns a DataFrame with one row per input policy and these prediction columns:

        expected_frequency_per_year : E[N_i | exposure=1]
        expected_severity           : E[S_i]                 (per claim)
        pure_premium_per_year       : E[N_i|1] × E[S_i]
        pure_premium_period         : E[N_i|actual exposure] × E[S_i]
        pricing_adequacy_ratio      : charged_premium / pure_premium_period
                                      >1 → charged above pure premium (margin)
                                      <1 → charged below pure premium (underpriced)
                                      NaN → charged_premium ≤ 0 (cancellation)
    """
    _log("Encoding features for prediction")
    X_freq = transform_features(policies, freq_bundle["encoder_meta"])
    X_sev  = transform_features(policies, sev_bundle["encoder_meta"])

    X_freq_c = sm.add_constant(X_freq, has_constant="add")
    X_sev_c  = sm.add_constant(X_sev,  has_constant="add")

    _log("Predicting frequency (per-year rate and period count)")
    # offset = 0 → predict annualized rate (per 1 year of exposure)
    # offset = log(exposure_years) → predict count over the actual policy period
    freq_per_year = freq_bundle["result"].predict(X_freq_c, offset=np.zeros(len(X_freq_c)))
    freq_period   = freq_bundle["result"].predict(
        X_freq_c, offset=np.log(policies["EXPOSURE_YEARS"].values)
    )

    _log("Predicting severity (per claim, ULTIMATE_CLAIM)")
    sev_pred = sev_bundle["result"].predict(X_sev_c)

    # Build the output frame
    out = policies[[
        "POLICY_NO", "POLICY_YEAR", "POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE",
        "EXPOSURE_YEARS", "TOTAL_PREMIUM", "COMMISSION",
        "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC", "SUM_INSURED", "SUM_INSURED_BAND",
        "IS_CANCELLED",
    ]].copy()

    out["expected_frequency_per_year"] = freq_per_year.values
    out["expected_frequency_period"]   = freq_period.values
    out["expected_severity"]           = sev_pred.values
    out["pure_premium_per_year"]       = out["expected_frequency_per_year"] * out["expected_severity"]
    out["pure_premium_period"]         = out["expected_frequency_period"]   * out["expected_severity"]
    out["charged_premium"]             = out["TOTAL_PREMIUM"]

    # Adequacy ratio — only meaningful for positive charged premium
    valid_prem = out["charged_premium"] > 0
    out["pricing_adequacy_ratio"] = np.where(
        valid_prem,
        out["charged_premium"] / out["pure_premium_period"].replace(0, np.nan),
        np.nan,
    )

    _log(f"  scored {len(out):,} policies")
    _log(f"  avg pure premium (annualized): SAR {out['pure_premium_per_year'].mean():,.0f}")
    _log(f"  avg charged premium:            SAR {out['charged_premium'].mean():,.0f}")

    # Portfolio-level calibration check
    ppremium_total = out["pure_premium_period"].sum()
    charged_total  = out.loc[valid_prem, "charged_premium"].sum()
    _log(f"  portfolio pure premium (period, sum): SAR {ppremium_total:,.0f}")
    _log(f"  portfolio charged premium (positive only): SAR {charged_total:,.0f}")
    _log(f"  portfolio adequacy: {charged_total / ppremium_total:.3f}")

    return out


# =========================================================================
# Segment aggregation
# =========================================================================
def aggregate_by_segment(scored: pd.DataFrame) -> pd.DataFrame:
    """
    Roll up to (ZONE, CLASS_DESC, POLICY_TYPE_DESC) and compute segment-level
    adequacy. Only positive-premium policies count in the numerator to avoid
    cancellations distorting the adequacy ratio.
    """
    _log("Aggregating to segment level")
    valid = scored[scored["charged_premium"] > 0].copy()

    agg = valid.groupby(SEGMENT_KEYS, dropna=False).agg(
        policy_count=("POLICY_NO", "count"),
        exposure_years=("EXPOSURE_YEARS", "sum"),
        charged_premium_total=("charged_premium", "sum"),
        pure_premium_period_total=("pure_premium_period", "sum"),
        avg_charged_premium=("charged_premium", "mean"),
        avg_pure_premium_period=("pure_premium_period", "mean"),
        avg_expected_frequency_per_year=("expected_frequency_per_year", "mean"),
        avg_expected_severity=("expected_severity", "mean"),
    ).reset_index()

    agg["adequacy_ratio"] = agg["charged_premium_total"] / agg["pure_premium_period_total"]
    agg["adequacy_pct_over_pure"] = (agg["adequacy_ratio"] - 1) * 100

    # Credibility flag based on policy count
    def _cred(n):
        if n >= FULL_CREDIBILITY_MIN_POLICIES:    return "Full"
        if n >= PARTIAL_CREDIBILITY_MIN_POLICIES: return "Partial"
        return "Low"
    agg["credibility_flag"] = agg["policy_count"].apply(_cred)

    # Pricing-position label
    def _pos(r):
        if pd.isna(r): return "N/A"
        if r < 0.80:   return "Severely underpriced"
        if r < 0.95:   return "Underpriced"
        if r <= 1.15:  return "Adequately priced"
        if r <= 1.40:  return "Overpriced"
        return "Severely overpriced"
    agg["pricing_position"] = agg["adequacy_ratio"].apply(_pos)

    agg = agg.sort_values("adequacy_ratio").reset_index(drop=True)
    _log(f"  segments produced: {len(agg)}")
    return agg


# =========================================================================
# Report
# =========================================================================
def write_report(scored: pd.DataFrame, segments: pd.DataFrame, n_missing_si: int) -> None:
    _log("Writing report")
    path = OUTPUTS_DIR / "pure_premium_report.md"

    valid = scored[scored["charged_premium"] > 0]
    port_charged = float(valid["charged_premium"].sum())
    port_pure = float(valid["pure_premium_period"].sum())
    port_adequacy = port_charged / port_pure

    # Get most under/overpriced segments among those with at least partial credibility
    credible = segments[segments["credibility_flag"].isin(["Full", "Partial"])].copy()
    most_under = credible.nsmallest(5, "adequacy_ratio")
    most_over  = credible.nlargest(5, "adequacy_ratio")

    with open(path, "w", encoding="utf-8") as f:
        f.write("# Pure Premium & Pricing Adequacy — v3\n\n")
        f.write("_Per-policy pure premium computed as E[N] × E[S] using the Phase 1 refitted GLMs._\n\n")

        f.write("## Portfolio-level view (motor only, positive premium)\n\n")
        f.write("| Metric | Value |\n|---|---|\n")
        f.write(f"| Policies scored | {len(valid):,} |\n")
        f.write(f"| Total charged premium | SAR {port_charged:,.0f} |\n")
        f.write(f"| Total pure premium (period) | SAR {port_pure:,.0f} |\n")
        f.write(f"| Portfolio adequacy ratio | **{port_adequacy:.3f}** |\n")
        f.write(f"| Portfolio position vs pure premium | {(port_adequacy - 1) * 100:+.1f}% |\n\n")

        f.write("Interpretation: an adequacy ratio of 1.00 means the portfolio charges exactly its expected ultimate claim cost. ")
        f.write("It does NOT include expenses or profit margin — that comparison lives in the break-even work.\n\n")

        f.write("## Most underpriced segments (with at least Partial credibility)\n\n")
        f.write("| Zone | Class | Type | Policies | Adequacy | vs Pure | Credibility |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for _, r in most_under.iterrows():
            f.write(f"| {r['ZONE']} | {r['CLASS_DESC']} | {r['POLICY_TYPE_DESC']} | {int(r['policy_count']):,} | "
                    f"{r['adequacy_ratio']:.3f} | {r['adequacy_pct_over_pure']:+.1f}% | {r['credibility_flag']} |\n")

        f.write("\n## Most overpriced segments (headroom for competitive pricing)\n\n")
        f.write("| Zone | Class | Type | Policies | Adequacy | vs Pure | Credibility |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for _, r in most_over.iterrows():
            f.write(f"| {r['ZONE']} | {r['CLASS_DESC']} | {r['POLICY_TYPE_DESC']} | {int(r['policy_count']):,} | "
                    f"{r['adequacy_ratio']:.3f} | {r['adequacy_pct_over_pure']:+.1f}% | {r['credibility_flag']} |\n")

        f.write("\n## Segment credibility summary\n\n")
        cred_counts = segments["credibility_flag"].value_counts()
        for level in ["Full", "Partial", "Low"]:
            f.write(f"- {level} credibility: {int(cred_counts.get(level, 0))} segments\n")

        f.write("\n## Definition-of-done check\n\n")
        f.write("| Check | Target | Result |\n|---|---|---|\n")
        f.write(f"| Per-policy predictions produced | one per motor policy-year | **PASS** ({len(scored):,} rows) |\n")
        f.write(f"| Predictions vary across policies | severity std > 0 | "
                f"**{'PASS' if scored['expected_severity'].std() > 0 else 'FAIL'}** "
                f"(σ = SAR {scored['expected_severity'].std():,.0f}) |\n")
        f.write(f"| Segment-level statement possible | ≥ 1 Full or Partial credibility segment | "
                f"**{'PASS' if len(credible) > 0 else 'FAIL'}** ({len(credible)} credible segments) |\n")

        f.write("\n## Caveats\n\n")
        f.write(f"1. **Missing SUM_INSURED_BAND** — {n_missing_si:,} policies were scored with SUM_INSURED_BAND='Unknown' "
                "because their sum insured is missing (intentional data-quality noise in the synthetic build). "
                "This matches how Phase 1 trained, so predictions are consistent, but the 'Unknown' bucket "
                "does not benefit from sum-insured signal.\n")
        f.write("2. **Adequacy ≠ profitability** — adequacy compares charged premium to expected ultimate claim cost only. "
                "The full profitability picture requires subtracting commission, admin expenses, and a risk margin. "
                "That is what `fact_segment_performance.csv` already provides.\n")
        f.write("3. **Portfolio adequacy < 1.0 does not mean the book is broken** — it means charged premium is below "
                "expected ultimate. On a synthetic dataset this is a function of the paid-to-ultimate assumptions in "
                "the build script. Change those assumptions and this number moves.\n")
        f.write("4. **Small segments carry wide uncertainty** — 'Low credibility' rows are directional only. "
                "Uncertainty intervals come in Phase 6.\n")

    _log(f"  wrote {path}")


# =========================================================================
# Entry point
# =========================================================================
def main():
    _log("=" * 60)
    _log("Pure premium (v3) starting")
    _log("=" * 60)

    freq_bundle, sev_bundle = load_models()
    policies = load_policies()
    n_missing_si = int(policies["SUM_INSURED_BAND"].isna().sum())

    scored = score_policies(policies, freq_bundle, sev_bundle)
    scored_path = FACT_DIR / "fact_policy_pricing.csv"
    scored.to_csv(scored_path, index=False)
    _log(f"  wrote {scored_path}")

    segments = aggregate_by_segment(scored)
    seg_path = OUTPUTS_DIR / "pricing_adequacy_by_segment.csv"
    segments.to_csv(seg_path, index=False)
    _log(f"  wrote {seg_path}")

    write_report(scored, segments, n_missing_si)

    _log("Done.")


if __name__ == "__main__":
    main()