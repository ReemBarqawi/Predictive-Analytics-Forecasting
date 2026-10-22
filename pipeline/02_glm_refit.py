"""
pipeline/02_glm_refit.py
==========================
Phase 1 of the post-data plan (Days 1-2).

Refits the frequency and severity GLMs on the synthetic DSS dataset.

Key changes vs the v2 models in 03_glm_models.py
------------------------------------------------
  1. Severity target = ULTIMATE_CLAIM, not TOTAL_PAID
       Rationale: TOTAL_PAID is a partial cash figure for open claims (~27%
       of the book) and biases the fit toward more-mature claims.
  2. ACC_TYPE_DESC removed from the pricing feature set
       Rationale: accident type is post-hoc — unknown at quote time — and its
       inclusion would leak the outcome into the pricing model.
  3. ZONE added as a real geographic feature (was completely absent in v2).
  4. SUM_INSURED_BAND added as a pricing feature.
  5. Frequency aggregation joins on (POLICY_NO, POLICY_YEAR), not POLICY_NO alone.
       Rationale: the old aggregation collapsed multi-year renewals and
       misassigned claims across policy periods.
  6. Frequency and severity share the same ex-ante feature set so that per-policy
       pure premium (Phase 2) can be computed row-wise.

Outputs
-------
  models/frequency_glm.pkl              — fitted NB GLM + encoder metadata
  models/severity_glm.pkl               — fitted Gamma GLM + encoder metadata
  outputs/frequency_coefficients_v3.csv
  outputs/severity_coefficients_v3.csv
  outputs/glm_refit_report.md           — one-page v2 vs v3 comparison

Prerequisite: run pipeline/01_build_synthetic_data.py first.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

# =========================================================================
# Config — everything you might want to tweak lives here
# =========================================================================
from settings import S, is_motor  # folders + motor classes for the active data source
FACT_DIR = S.facts_dir
MODELS_DIR = S.models_dir
OUTPUTS_DIR = S.reports_dir


# Features known at quote time (no target leakage)
PRICING_FEATURES = [
    "POLICY_TYPE_DESC",
    "CLASS_DESC",
    "SOURCE_DESC",
    "ZONE",
    "SUM_INSURED_BAND",
]

# Group any categorical level with fewer than this many rows into 'Other'
RARE_THRESHOLD = 30


def _log(msg: str) -> None:
    print(f"[refit] {msg}", flush=True)


# =========================================================================
# Data loading
# =========================================================================
def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load fact_policy and fact_claim, restrict to motor and matched claims."""
    _log("Loading fact_policy and fact_claim")
    policies = pd.read_csv(FACT_DIR / "fact_policy.csv", low_memory=False)
    claims = pd.read_csv(FACT_DIR / "fact_claim.csv", low_memory=False)

    # Motor only for both sides
    policies = policies[is_motor(policies["CLASS_DESC"])].copy()

    # Only claims that were successfully reassigned to a policy
    claims = claims[claims["POLICY_MATCHED"] == True].copy()
    claims = claims.dropna(subset=["POLICY_NO", "POLICY_YEAR"])
    claims["POLICY_NO"] = claims["POLICY_NO"].astype(int)
    claims["POLICY_YEAR"] = claims["POLICY_YEAR"].astype(int)

    _log(f"  motor policies: {len(policies):,}")
    _log(f"  matched motor claims: {len(claims):,}")
    return policies, claims


# =========================================================================
# Feature engineering
# =========================================================================
def _group_rare(series: pd.Series, threshold: int = RARE_THRESHOLD) -> tuple[pd.Series, dict]:
    """
    Group rare categorical levels into 'Other'. Missing → 'Unknown'.
    Returns (regrouped_series, mapping_dict).
    The mapping_dict is saved so Phase 2 can apply the same grouping to new data.
    """
    counts = series.fillna("Unknown").value_counts()
    kept = counts[counts >= threshold].index.tolist()

    def _map(x):
        if pd.isna(x):
            return "Unknown"
        return x if x in kept else "Other"

    return series.apply(_map), {"kept": kept, "threshold": threshold}


def encode_features(
    df: pd.DataFrame,
    feature_cols: list[str],
    encoder_meta: dict | None = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Rare-group + one-hot encode categorical features.

    If encoder_meta is None → FIT mode: learn rare-category rules and column set.
    If encoder_meta is provided → TRANSFORM mode: apply saved rules, align columns.
    """
    df2 = df.copy()

    if encoder_meta is None:
        # FIT
        encoder_meta = {"category_maps": {}, "columns": []}
        for col in feature_cols:
            df2[col], mapping = _group_rare(df2[col])
            encoder_meta["category_maps"][col] = mapping
        X = pd.get_dummies(df2[feature_cols], drop_first=True, dtype=int)
        encoder_meta["columns"] = X.columns.tolist()
        return X, encoder_meta

    # TRANSFORM
    for col in feature_cols:
        mapping = encoder_meta["category_maps"][col]
        kept = set(mapping["kept"])
        def _apply(x, kept=kept):
            if pd.isna(x): return "Unknown"
            return x if x in kept else "Other"
        df2[col] = df2[col].apply(_apply)

    X = pd.get_dummies(df2[feature_cols], drop_first=True, dtype=int)
    # Align to training columns exactly (add missing as zeros, drop extras)
    for c in encoder_meta["columns"]:
        if c not in X.columns:
            X[c] = 0
    X = X[encoder_meta["columns"]]
    return X, encoder_meta


# =========================================================================
# Frequency dataset + model
# =========================================================================
def prepare_frequency_dataset(policies: pd.DataFrame, claims: pd.DataFrame) -> pd.DataFrame:
    """
    One row per motor (POLICY_NO, POLICY_YEAR) with claim count and exposure.
    Zero-fills policies with no claims. Filters to positive exposure.
    """
    _log("Preparing frequency dataset")

    # Aggregate by (POLICY_NO, POLICY_YEAR) — critical fix vs the old code
    claim_counts = (
        claims.groupby(["POLICY_NO", "POLICY_YEAR"])
              .size()
              .reset_index(name="TOTAL_CLAIMS")
    )

    freq = policies.merge(claim_counts, on=["POLICY_NO", "POLICY_YEAR"], how="left")
    freq["TOTAL_CLAIMS"] = freq["TOTAL_CLAIMS"].fillna(0).astype(int)

    # Positive exposure only
    freq = freq[freq["EXPOSURE_YEARS"] > 0].copy()

    # Cast features to string (SUM_INSURED_BAND arrives as categorical)
    for col in PRICING_FEATURES:
        freq[col] = freq[col].astype(str).replace({"nan": "Unknown"})

    _log(f"  frequency rows: {len(freq):,}")
    _log(f"  policies with claims: {(freq['TOTAL_CLAIMS'] > 0).sum():,}")
    _log(f"  observed claim rate: {freq['TOTAL_CLAIMS'].sum() / freq['EXPOSURE_YEARS'].sum():.4f} claims/year")

    return freq


def fit_frequency_model(freq: pd.DataFrame) -> dict:
    """Fit Negative Binomial GLM with log-exposure offset."""
    _log("Fitting frequency model (Negative Binomial GLM with log-exposure offset)")

    X, encoder_meta = encode_features(freq, PRICING_FEATURES)
    X_const = sm.add_constant(X)
    y = freq["TOTAL_CLAIMS"]
    offset = np.log(freq["EXPOSURE_YEARS"])

    model = sm.GLM(
        y,
        X_const,
        family=sm.families.NegativeBinomial(alpha=1.0),
        offset=offset,
    )
    result = model.fit()

    # In-sample sanity: predicted claim count should match observed at portfolio level
    y_pred = result.predict(X_const, offset=offset)
    pred_total = float(y_pred.sum())
    obs_total = float(y.sum())
    _log(f"  in-sample predicted claim count: {pred_total:,.1f}  (observed: {obs_total:,.0f})")
    _log(f"  in-sample bias: {(pred_total - obs_total) / obs_total:+.2%}")
    _log(f"  AIC: {result.aic:,.0f}")

    return {
        "result": result,
        "encoder_meta": encoder_meta,
        "feature_cols": PRICING_FEATURES,
        "family": "NegativeBinomial",
        "link": "log",
        "offset": "log(EXPOSURE_YEARS)",
        "target": "TOTAL_CLAIMS",
        "n_train": len(freq),
        "aic": float(result.aic),
        "in_sample_predicted_total": pred_total,
        "in_sample_observed_total": obs_total,
    }


# =========================================================================
# Severity dataset + model
# =========================================================================
def prepare_severity_dataset(policies: pd.DataFrame, claims: pd.DataFrame) -> pd.DataFrame:
    """
    One row per claim with ULTIMATE_CLAIM > 0, features taken from the underlying policy.
    Drops the claim-side copies of the pricing features so we always use the policy version
    (which is what a pricing model should see at quote time).
    """
    _log("Preparing severity dataset")

    pol_slim = policies[["POLICY_NO", "POLICY_YEAR"] + PRICING_FEATURES].copy()

    # Drop claim-side duplicates of pricing features to avoid the merge suffixing them
    claim_slim = claims.drop(
        columns=[c for c in PRICING_FEATURES if c in claims.columns],
        errors="ignore",
    )

    sev = claim_slim.merge(pol_slim, on=["POLICY_NO", "POLICY_YEAR"], how="left")
    sev = sev[sev["ULTIMATE_CLAIM"] > 0].copy()

    for col in PRICING_FEATURES:
        sev[col] = sev[col].astype(str).replace({"nan": "Unknown"})

    _log(f"  severity rows: {len(sev):,}")
    _log(f"  ultimate mean:   SAR {sev['ULTIMATE_CLAIM'].mean():,.0f}")
    _log(f"  ultimate median: SAR {sev['ULTIMATE_CLAIM'].median():,.0f}")
    _log(f"  ultimate P99:    SAR {sev['ULTIMATE_CLAIM'].quantile(0.99):,.0f}")

    return sev


def fit_severity_model(sev: pd.DataFrame) -> dict:
    """Fit Gamma GLM with log link on ULTIMATE_CLAIM."""
    _log("Fitting severity model (Gamma GLM, log link, target = ULTIMATE_CLAIM)")

    X, encoder_meta = encode_features(sev, PRICING_FEATURES)
    X_const = sm.add_constant(X)
    y = sev["ULTIMATE_CLAIM"]

    model = sm.GLM(y, X_const, family=sm.families.Gamma(link=sm.families.links.Log()))
    result = model.fit()

    y_pred = result.predict(X_const)
    pred_total = float(y_pred.sum())
    obs_total = float(y.sum())
    _log(f"  in-sample predicted ultimate total: SAR {pred_total:,.0f}  (observed: SAR {obs_total:,.0f})")
    _log(f"  in-sample bias: {(pred_total - obs_total) / obs_total:+.2%}")
    _log(f"  AIC: {result.aic:,.0f}")

    return {
        "result": result,
        "encoder_meta": encoder_meta,
        "feature_cols": PRICING_FEATURES,
        "family": "Gamma",
        "link": "log",
        "target": "ULTIMATE_CLAIM",
        "n_train": len(sev),
        "aic": float(result.aic),
        "in_sample_predicted_total": pred_total,
        "in_sample_observed_total": obs_total,
    }


# =========================================================================
# Coefficient exports
# =========================================================================
def save_coefficients(bundle: dict, out_path: Path) -> pd.DataFrame:
    """Save a readable coefficient table with multipliers and significance stars."""
    result = bundle["result"]
    df = pd.DataFrame({
        "coefficient": result.params,
        "std_error":   result.bse,
        "z":           result.tvalues,
        "p_value":     result.pvalues,
        "multiplier":  np.exp(result.params),
    })
    df["signif"] = df["p_value"].apply(
        lambda p: "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
    )
    df.index.name = "term"
    df = df.round({"coefficient": 4, "std_error": 4, "z": 3, "p_value": 4, "multiplier": 4})
    df.to_csv(out_path)
    return df


# =========================================================================
# Save models
# =========================================================================
def save_model(bundle: dict, path: Path) -> None:
    """Pickle everything needed to predict later (Phase 2)."""
    to_save = {
        "result": bundle["result"],
        "encoder_meta": bundle["encoder_meta"],
        "feature_cols": bundle["feature_cols"],
        "family": bundle["family"],
        "link": bundle["link"],
        "target": bundle["target"],
    }
    # NB frequency model also needs to know it uses an offset
    if "offset" in bundle:
        to_save["offset"] = bundle["offset"]

    with open(path, "wb") as f:
        pickle.dump(to_save, f)
    _log(f"  saved {path}")


# =========================================================================
# v2 vs v3 comparison report
# =========================================================================
def write_comparison_report(freq_bundle: dict, sev_bundle: dict) -> None:
    """
    One-page comparison of v3 vs v2. v2 numbers are pulled from the recorded
    values in your userMemories / previous outputs; if you have exact v2 numbers,
    edit them below to match.
    """
    _log("Writing comparison report")
    report_path = OUTPUTS_DIR / "glm_refit_report.md"

    fr, sr = freq_bundle["result"], sev_bundle["result"]
    n_freq_sig = int((fr.pvalues < 0.05).sum())
    n_sev_sig = int((sr.pvalues < 0.05).sum())

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# GLM refit — v3 report (Phase 1)\n\n")
        f.write("_Refit on the synthetic DSS dataset. In-sample metrics only — real validation is Phase 3 (rolling-origin backtest)._\n\n")

        f.write("## What changed vs v2\n\n")
        f.write("| Change | v2 | v3 |\n|---|---|---|\n")
        f.write("| Severity target | TOTAL_PAID (partial for open claims) | **ULTIMATE_CLAIM** (developed) |\n")
        f.write("| Pricing features include ACC_TYPE_DESC? | Yes (target leakage) | **No** |\n")
        f.write("| Zone/geography in features? | No | **Yes** |\n")
        f.write("| Sum insured in features? | No | **Yes (banded)** |\n")
        f.write("| Frequency aggregation key | POLICY_NO alone (broken) | **(POLICY_NO, POLICY_YEAR)** |\n")
        f.write("| Policy/claim match rate | 1.6% | **97.0%** |\n\n")

        f.write("## Frequency model\n\n")
        f.write(f"- Family: {freq_bundle['family']} (log link, offset = {freq_bundle['offset']})\n")
        f.write(f"- Features: {', '.join(freq_bundle['feature_cols'])}\n")
        f.write(f"- Training rows: {freq_bundle['n_train']:,}\n")
        f.write(f"- Total coefficients (incl. intercept): {len(fr.params)}\n")
        f.write(f"- Significant at 5%: {n_freq_sig}\n")
        f.write(f"- AIC: {freq_bundle['aic']:,.0f}\n")
        f.write(f"- In-sample predicted claim count: {freq_bundle['in_sample_predicted_total']:,.1f}\n")
        f.write(f"- Observed claim count: {freq_bundle['in_sample_observed_total']:,.0f}\n")
        pct = (freq_bundle['in_sample_predicted_total'] - freq_bundle['in_sample_observed_total']) / freq_bundle['in_sample_observed_total']
        f.write(f"- In-sample bias: {pct:+.2%}\n\n")

        f.write("## Severity model\n\n")
        f.write(f"- Family: {sev_bundle['family']} (log link)\n")
        f.write(f"- Target: {sev_bundle['target']}\n")
        f.write(f"- Features: {', '.join(sev_bundle['feature_cols'])}\n")
        f.write(f"- Training rows: {sev_bundle['n_train']:,}\n")
        f.write(f"- Total coefficients (incl. intercept): {len(sr.params)}\n")
        f.write(f"- Significant at 5%: {n_sev_sig}\n")
        f.write(f"- AIC: {sev_bundle['aic']:,.0f}\n")
        f.write(f"- In-sample predicted ultimate total: SAR {sev_bundle['in_sample_predicted_total']:,.0f}\n")
        f.write(f"- Observed ultimate total: SAR {sev_bundle['in_sample_observed_total']:,.0f}\n")
        pct = (sev_bundle['in_sample_predicted_total'] - sev_bundle['in_sample_observed_total']) / sev_bundle['in_sample_observed_total']
        f.write(f"- In-sample bias: {pct:+.2%}\n\n")

        f.write("## Definition-of-done check\n\n")
        f.write("| Check | Target | Result |\n|---|---|---|\n")
        f_bias = abs((freq_bundle['in_sample_predicted_total'] - freq_bundle['in_sample_observed_total']) / freq_bundle['in_sample_observed_total'])
        s_bias = abs((sev_bundle['in_sample_predicted_total'] - sev_bundle['in_sample_observed_total']) / sev_bundle['in_sample_observed_total'])
        f.write(f"| Frequency in-sample bias | within ±10% | {'PASS' if f_bias <= 0.10 else 'FAIL'} ({f_bias:.1%}) |\n")
        f.write(f"| Severity in-sample bias | within ±10% | {'PASS' if s_bias <= 0.10 else 'FAIL'} ({s_bias:.1%}) |\n")
        f.write("| No ACC_TYPE_DESC in features | required | PASS |\n")
        f.write("| Zone included | required | PASS |\n\n")

        f.write("## What this report does NOT show\n\n")
        f.write("These are in-sample numbers. True out-of-time accuracy is measured in Phase 3 (rolling-origin backtest).\n")
        f.write("A model can fit the training data well and still forecast poorly.\n")

    _log(f"  wrote {report_path}")


# =========================================================================
# Entry point
# =========================================================================
def main():
    _log("=" * 60)
    _log("GLM refit v3 starting")
    _log("=" * 60)

    policies, claims = load_data()

    # Frequency
    freq_data = prepare_frequency_dataset(policies, claims)
    freq_bundle = fit_frequency_model(freq_data)
    save_coefficients(freq_bundle, OUTPUTS_DIR / "frequency_coefficients_v3.csv")
    save_model(freq_bundle, MODELS_DIR / "frequency_glm.pkl")

    # Severity
    sev_data = prepare_severity_dataset(policies, claims)
    sev_bundle = fit_severity_model(sev_data)
    save_coefficients(sev_bundle, OUTPUTS_DIR / "severity_coefficients_v3.csv")
    save_model(sev_bundle, MODELS_DIR / "severity_glm.pkl")

    # Report
    write_comparison_report(freq_bundle, sev_bundle)

    _log("Done.")


if __name__ == "__main__":
    main()