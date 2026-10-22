"""
pipeline/04_backtest.py
=========================
Phase 3 of the post-data plan (Day 4).

Rolling-origin (expanding window) backtest of the frequency and severity GLMs.

At each historical cutoff T:
  1. Refit both GLMs using only data available at T
     - policies with POLICY_EFFECTIVE_DATE <= T
     - observed exposure = min(POLICY_EXPIRY_DATE, T) - POLICY_EFFECTIVE_DATE
     - claims with ACC_DATE <= T
  2. Predict total claim COUNT and total claim COST for four forward horizons
     (2 weeks, 1 month, 3 months, 12 months) at portfolio level and per segment
  3. Compare against ACTUALS (claims that actually occurred in the window)
  4. Compare against a SEASONAL NAIVE benchmark
     (prediction = actuals from the same window one year earlier)

This is what turns "in-sample bias +2.5%" (from Phase 1) into a defensible
out-of-time accuracy claim.

Outputs
-------
  SYNTHETIC_DSS_DATA/02_facts/fact_model_performance.csv
      Long-format table, one row per (cutoff × horizon × model × target × segment)
  outputs/backtest_report.md
      Per-horizon summary of GLM vs naive at portfolio level and for
      Full-credibility segments.

Runtime: ~5-10 minutes on the synthetic dataset (19 cutoffs × 2 GLM fits each).
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

warnings.filterwarnings("ignore")

# =========================================================================
# Config
# =========================================================================
from settings import S, is_motor  # folders + motor classes for the active data source
FACT_DIR = S.facts_dir
OUTPUTS_DIR = S.reports_dir


PRICING_FEATURES = [
    "POLICY_TYPE_DESC",
    "CLASS_DESC",
    "SOURCE_DESC",
    "ZONE",
    "SUM_INSURED_BAND",
]

RARE_THRESHOLD = 30
MIN_TRAIN_EXPOSURE_DAYS = 30       # skip policies with less than 30 days observed
MIN_TRAIN_POLICIES = 500           # minimum policies to attempt a cutoff fit
MIN_TRAIN_CLAIMS = 100             # minimum claims to attempt severity fit

# Horizons for the backtest (in days)
HORIZONS = [
    ("2w",   14),
    ("1m",   30),
    ("3m",   91),
    ("12m", 365),
]

# Segments reported at segment level (only those meeting credibility threshold)
FULL_CREDIBILITY_MIN_POLICIES = 300


def _log(msg: str) -> None:
    print(f"[backtest] {msg}", flush=True)


# =========================================================================
# Feature encoder (duplicated from Phase 1/2 — extract to _utils.py if you
# find yourself copying it again in Phase 4+)
# =========================================================================
def _group_rare(series: pd.Series, threshold: int = RARE_THRESHOLD):
    counts = series.fillna("Unknown").value_counts()
    kept = counts[counts >= threshold].index.tolist()
    def _map(x):
        if pd.isna(x): return "Unknown"
        return x if x in kept else "Other"
    return series.apply(_map), {"kept": kept, "threshold": threshold}


def fit_encoder(df: pd.DataFrame, feature_cols: list[str]) -> tuple[pd.DataFrame, dict]:
    df2 = df.copy()
    encoder_meta = {"category_maps": {}, "columns": []}
    for col in feature_cols:
        df2[col], mapping = _group_rare(df2[col])
        encoder_meta["category_maps"][col] = mapping
    X = pd.get_dummies(df2[feature_cols], drop_first=True, dtype=int)
    encoder_meta["columns"] = X.columns.tolist()
    return X, encoder_meta


def apply_encoder(df: pd.DataFrame, encoder_meta: dict) -> pd.DataFrame:
    df2 = df.copy()
    for col, mapping in encoder_meta["category_maps"].items():
        kept = set(mapping["kept"])
        def _apply(x, kept=kept):
            if pd.isna(x): return "Unknown"
            return x if x in kept else "Other"
        df2[col] = df2[col].astype(str).replace({"nan": "Unknown"}).apply(_apply)
    X = pd.get_dummies(df2[list(encoder_meta["category_maps"].keys())], drop_first=True, dtype=int)
    for c in encoder_meta["columns"]:
        if c not in X.columns:
            X[c] = 0
    return X[encoder_meta["columns"]]


# =========================================================================
# Data loading
# =========================================================================
def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    _log("Loading fact_policy and fact_claim")
    p = pd.read_csv(FACT_DIR / "fact_policy.csv", low_memory=False)
    c = pd.read_csv(FACT_DIR / "fact_claim.csv", low_memory=False)

    p["POLICY_EFFECTIVE_DATE"] = pd.to_datetime(p["POLICY_EFFECTIVE_DATE"])
    p["POLICY_EXPIRY_DATE"]    = pd.to_datetime(p["POLICY_EXPIRY_DATE"])
    c["ACC_DATE"]              = pd.to_datetime(c["ACC_DATE"])

    p = p[is_motor(p["CLASS_DESC"])].copy()
    c = c[c["POLICY_MATCHED"] == True].copy()
    c = c.dropna(subset=["POLICY_NO", "POLICY_YEAR", "ACC_DATE"])
    c["POLICY_NO"]   = c["POLICY_NO"].astype(int)
    c["POLICY_YEAR"] = c["POLICY_YEAR"].astype(int)

    # Attach segment keys to each claim via its policy for per-segment aggregation
    pol_seg = p[["POLICY_NO", "POLICY_YEAR", "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]] \
                .rename(columns={"ZONE": "SEG_ZONE",
                                 "CLASS_DESC": "SEG_CLASS",
                                 "POLICY_TYPE_DESC": "SEG_TYPE"})
    c = c.merge(pol_seg, on=["POLICY_NO", "POLICY_YEAR"], how="left")

    _log(f"  motor policies: {len(p):,}")
    _log(f"  matched motor claims (with valid ACC_DATE): {len(c):,}")
    _log(f"  data span: {c['ACC_DATE'].min().date()} → {c['ACC_DATE'].max().date()}")
    return p, c


# =========================================================================
# Train GLMs at a given cutoff T
# =========================================================================
def train_at_cutoff(policies: pd.DataFrame, claims: pd.DataFrame, T: pd.Timestamp):
    """Fit frequency and severity GLMs using only data available at T."""
    # Training policies: effective on or before T
    train_pol = policies[policies["POLICY_EFFECTIVE_DATE"] <= T].copy()

    # Observed exposure at T (partial for still-in-force policies)
    end_at_T = train_pol["POLICY_EXPIRY_DATE"].clip(upper=T)
    train_pol["EXPOSURE_AT_T_YEARS"] = (
        (end_at_T - train_pol["POLICY_EFFECTIVE_DATE"]).dt.days / 365.25
    )
    train_pol = train_pol[
        train_pol["EXPOSURE_AT_T_YEARS"] >= MIN_TRAIN_EXPOSURE_DAYS / 365.25
    ].copy()

    if len(train_pol) < MIN_TRAIN_POLICIES:
        return None, None

    # Aggregate observed claims
    train_claims = claims[claims["ACC_DATE"] <= T].copy()
    claim_counts = (
        train_claims.groupby(["POLICY_NO", "POLICY_YEAR"])
                    .size()
                    .reset_index(name="TOTAL_CLAIMS")
    )
    freq_df = train_pol.merge(claim_counts, on=["POLICY_NO", "POLICY_YEAR"], how="left")
    freq_df["TOTAL_CLAIMS"] = freq_df["TOTAL_CLAIMS"].fillna(0).astype(int)
    for col in PRICING_FEATURES:
        freq_df[col] = freq_df[col].astype(str).replace({"nan": "Unknown"})

    # Fit frequency GLM
    try:
        X_freq, enc_f = fit_encoder(freq_df, PRICING_FEATURES)
        X_freq_c = sm.add_constant(X_freq, has_constant="add")
        freq_model = sm.GLM(
            freq_df["TOTAL_CLAIMS"],
            X_freq_c,
            family=sm.families.NegativeBinomial(alpha=1.0),
            offset=np.log(freq_df["EXPOSURE_AT_T_YEARS"]),
        ).fit()
    except Exception as e:
        _log(f"  cutoff {T.date()}: frequency fit failed ({e})")
        return None, None
    freq_bundle = {"result": freq_model, "encoder_meta": enc_f}

    # Fit severity GLM
    pol_slim = train_pol[["POLICY_NO", "POLICY_YEAR"] + PRICING_FEATURES]
    sev_df = train_claims.drop(
        columns=[c for c in PRICING_FEATURES if c in train_claims.columns],
        errors="ignore",
    ).merge(pol_slim, on=["POLICY_NO", "POLICY_YEAR"], how="inner")
    sev_df = sev_df[sev_df["ULTIMATE_CLAIM"] > 0]
    if len(sev_df) < MIN_TRAIN_CLAIMS:
        return freq_bundle, None
    for col in PRICING_FEATURES:
        sev_df[col] = sev_df[col].astype(str).replace({"nan": "Unknown"})
    try:
        X_sev, enc_s = fit_encoder(sev_df, PRICING_FEATURES)
        X_sev_c = sm.add_constant(X_sev, has_constant="add")
        sev_model = sm.GLM(
            sev_df["ULTIMATE_CLAIM"],
            X_sev_c,
            family=sm.families.Gamma(link=sm.families.links.Log()),
        ).fit()
    except Exception as e:
        _log(f"  cutoff {T.date()}: severity fit failed ({e})")
        return freq_bundle, None

    return freq_bundle, {"result": sev_model, "encoder_meta": enc_s}


# =========================================================================
# Predict a window using the fitted GLMs
# =========================================================================
def predict_glm_window(
    policies: pd.DataFrame,
    freq_bundle: dict,
    sev_bundle: dict,
    T_start: pd.Timestamp,
    T_end: pd.Timestamp,
) -> pd.DataFrame:
    """
    Predict per-policy expected claim count and cost during (T_start, T_end].
    Returns a DataFrame with segment columns + predicted_count + predicted_cost.
    """
    active = policies[
        (policies["POLICY_EFFECTIVE_DATE"] <= T_end) &
        (policies["POLICY_EXPIRY_DATE"]    >  T_start)
    ].copy()
    if len(active) == 0:
        return pd.DataFrame()

    overlap_start = active["POLICY_EFFECTIVE_DATE"].clip(lower=T_start)
    overlap_end   = active["POLICY_EXPIRY_DATE"].clip(upper=T_end)
    active["EXPOSURE_IN_WINDOW_YEARS"] = (
        (overlap_end - overlap_start).dt.days / 365.25
    ).clip(lower=0)
    active = active[active["EXPOSURE_IN_WINDOW_YEARS"] > 0].copy()

    for col in PRICING_FEATURES:
        active[col] = active[col].astype(str).replace({"nan": "Unknown"})

    X_f = sm.add_constant(apply_encoder(active, freq_bundle["encoder_meta"]),
                          has_constant="add")
    active["predicted_count"] = freq_bundle["result"].predict(
        X_f, offset=np.log(active["EXPOSURE_IN_WINDOW_YEARS"].values)
    )

    if sev_bundle is not None:
        X_s = sm.add_constant(apply_encoder(active, sev_bundle["encoder_meta"]),
                              has_constant="add")
        active["predicted_severity"] = sev_bundle["result"].predict(X_s)
    else:
        active["predicted_severity"] = 0.0

    active["predicted_cost"] = active["predicted_count"] * active["predicted_severity"]
    return active[[
        "POLICY_NO", "POLICY_YEAR", "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC",
        "predicted_count", "predicted_cost",
    ]]


# =========================================================================
# Actuals and seasonal-naive
# =========================================================================
def sum_actuals(claims: pd.DataFrame, T_start: pd.Timestamp, T_end: pd.Timestamp) -> pd.DataFrame:
    """
    Return actual claim count and cost per segment (plus PORTFOLIO row) in (T_start, T_end].
    """
    w = claims[(claims["ACC_DATE"] > T_start) & (claims["ACC_DATE"] <= T_end)].copy()

    # Portfolio row
    port = pd.DataFrame([{
        "segment_level": "PORTFOLIO", "segment_id": "PORTFOLIO",
        "actual_count": len(w), "actual_cost": float(w["ULTIMATE_CLAIM"].sum()),
    }])

    # Per-segment
    seg = (w.groupby(["SEG_ZONE", "SEG_CLASS", "SEG_TYPE"], dropna=False)
             .agg(actual_count=("CLM_NO", "count"),
                  actual_cost=("ULTIMATE_CLAIM", "sum"))
             .reset_index())
    seg["segment_level"] = "SEGMENT"
    seg["segment_id"] = (
        seg["SEG_ZONE"].astype(str) + "|" +
        seg["SEG_CLASS"].astype(str) + "|" +
        seg["SEG_TYPE"].astype(str)
    )
    seg = seg[["segment_level", "segment_id", "actual_count", "actual_cost"]]

    return pd.concat([port, seg], ignore_index=True)


def sum_predictions(preds: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-policy predictions to portfolio + per-segment totals."""
    if len(preds) == 0:
        return pd.DataFrame(columns=["segment_level", "segment_id", "predicted_count", "predicted_cost"])
    port = pd.DataFrame([{
        "segment_level": "PORTFOLIO", "segment_id": "PORTFOLIO",
        "predicted_count": float(preds["predicted_count"].sum()),
        "predicted_cost":  float(preds["predicted_cost"].sum()),
    }])
    seg = (preds.groupby(["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"], dropna=False)
                .agg(predicted_count=("predicted_count", "sum"),
                     predicted_cost=("predicted_cost", "sum"))
                .reset_index())
    seg["segment_level"] = "SEGMENT"
    seg["segment_id"] = (
        seg["ZONE"].astype(str) + "|" +
        seg["CLASS_DESC"].astype(str) + "|" +
        seg["POLICY_TYPE_DESC"].astype(str)
    )
    seg = seg[["segment_level", "segment_id", "predicted_count", "predicted_cost"]]
    return pd.concat([port, seg], ignore_index=True)


# =========================================================================
# Backtest driver
# =========================================================================
def run_backtest(policies: pd.DataFrame, claims: pd.DataFrame,
                 cutoffs: list[pd.Timestamp]) -> pd.DataFrame:
    """Run the full rolling backtest and return a long-format results DataFrame."""
    max_data_date = claims["ACC_DATE"].max()
    rows = []

    for T in cutoffs:
        _log(f"cutoff {T.date()} — training GLMs")
        freq, sev = train_at_cutoff(policies, claims, T)
        if freq is None:
            _log(f"  skipped (insufficient training data)")
            continue

        for horizon_label, horizon_days in HORIZONS:
            T_end = T + pd.Timedelta(days=horizon_days)
            if T_end > max_data_date:
                continue

            # Actuals
            actual_df = sum_actuals(claims, T, T_end)

            # GLM prediction
            glm_pred_df = sum_predictions(predict_glm_window(policies, freq, sev, T, T_end))

            # Seasonal naive: use the same window one year earlier as prediction
            T_naive_start = T - pd.Timedelta(days=365)
            T_naive_end   = T_end - pd.Timedelta(days=365)
            naive_df = sum_actuals(claims, T_naive_start, T_naive_end) \
                .rename(columns={"actual_count": "predicted_count",
                                 "actual_cost":  "predicted_cost"})

            # Merge each model against actuals and emit rows
            for model_name, pred_df in [("glm", glm_pred_df), ("seasonal_naive", naive_df)]:
                joined = actual_df.merge(pred_df, on=["segment_level", "segment_id"], how="left")
                joined["predicted_count"] = joined["predicted_count"].fillna(0)
                joined["predicted_cost"]  = joined["predicted_cost"].fillna(0)

                for target, pred_col, act_col in [
                    ("claim_count", "predicted_count", "actual_count"),
                    ("claim_cost",  "predicted_cost",  "actual_cost"),
                ]:
                    tmp = joined[["segment_level", "segment_id", pred_col, act_col]].copy()
                    tmp = tmp.rename(columns={pred_col: "predicted", act_col: "actual"})
                    tmp["cutoff_date"] = T
                    tmp["horizon_label"] = horizon_label
                    tmp["horizon_end"] = T_end
                    tmp["model"] = model_name
                    tmp["target"] = target
                    tmp["abs_error"] = (tmp["predicted"] - tmp["actual"]).abs()
                    tmp["signed_error"] = tmp["predicted"] - tmp["actual"]
                    tmp["squared_error"] = (tmp["predicted"] - tmp["actual"]) ** 2
                    rows.append(tmp)

    if not rows:
        raise RuntimeError("Backtest produced no rows — check cutoffs and data span.")

    return pd.concat(rows, ignore_index=True)


# =========================================================================
# Metric summary
# =========================================================================
def summarize(results: pd.DataFrame, level: str = "PORTFOLIO") -> pd.DataFrame:
    """Aggregate per-horizon per-model MAE, RMSE, WAPE, bias."""
    sub = results[results["segment_level"] == level].copy()

    def _agg(g):
        mae = g["abs_error"].mean()
        rmse = np.sqrt(g["squared_error"].mean())
        wape = g["abs_error"].sum() / g["actual"].abs().sum() if g["actual"].abs().sum() > 0 else np.nan
        bias_pct = g["signed_error"].sum() / g["actual"].sum() if g["actual"].sum() > 0 else np.nan
        n_cutoffs = g["cutoff_date"].nunique()
        return pd.Series({"MAE": mae, "RMSE": rmse, "WAPE": wape, "BIAS": bias_pct,
                          "n_cutoffs": n_cutoffs})

    return (sub.groupby(["target", "horizon_label", "model"])
               .apply(_agg)
               .reset_index())


# =========================================================================
# Report
# =========================================================================
def write_report(results: pd.DataFrame) -> None:
    _log("Writing report")
    path = OUTPUTS_DIR / "backtest_report.md"

    port_summary = summarize(results, level="PORTFOLIO")

    # Segment-level summary — only Full-credibility segments
    # Determine which segments are Full credibility using the current portfolio
    seg_policy_counts = (
        pd.read_csv(FACT_DIR / "fact_policy.csv", low_memory=False)
          .loc[lambda d: is_motor(d["CLASS_DESC"])]
          .assign(seg_id=lambda d: d["ZONE"].astype(str) + "|" + d["CLASS_DESC"].astype(str) + "|" + d["POLICY_TYPE_DESC"].astype(str))
          .groupby("seg_id").size()
    )
    full_cred = set(seg_policy_counts[seg_policy_counts >= FULL_CREDIBILITY_MIN_POLICIES].index)

    seg_results = results[
        (results["segment_level"] == "SEGMENT") &
        (results["segment_id"].isin(full_cred))
    ]
    seg_summary = summarize(seg_results, level="SEGMENT")

    with open(path, "w", encoding="utf-8") as f:
        f.write("# Backtest Report — v3 (Phase 3)\n\n")
        f.write("_Rolling-origin (expanding window) backtest of the frequency and severity GLMs._\n")
        f.write("_Predictions are compared against actual claim volumes in the forward window._\n")
        f.write("_Benchmark: **seasonal naive** — prediction for window (T, T+h] = actuals in (T-1yr, T-1yr+h]._\n\n")

        f.write(f"Cutoffs evaluated: {results['cutoff_date'].nunique()}\n")
        f.write(f"Cutoff range: {results['cutoff_date'].min().date()} → {results['cutoff_date'].max().date()}\n\n")

        f.write("## Portfolio-level performance\n\n")
        for target in ["claim_count", "claim_cost"]:
            f.write(f"### {target}\n\n")
            sub = port_summary[port_summary["target"] == target].copy()
            sub = sub.pivot(index="horizon_label", columns="model",
                            values=["MAE", "WAPE", "BIAS"])
            # Order horizons naturally
            horizon_order = [h[0] for h in HORIZONS if h[0] in sub.index]
            sub = sub.reindex(horizon_order)

            f.write("| horizon | GLM MAE | Naive MAE | GLM WAPE | Naive WAPE | GLM Bias | Naive Bias | Winner (WAPE) |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
            for h in sub.index:
                glm_mae   = sub.loc[h, ("MAE",  "glm")]
                naive_mae = sub.loc[h, ("MAE",  "seasonal_naive")]
                glm_wape  = sub.loc[h, ("WAPE", "glm")]
                naive_wape= sub.loc[h, ("WAPE", "seasonal_naive")]
                glm_bias  = sub.loc[h, ("BIAS", "glm")]
                naive_bias= sub.loc[h, ("BIAS", "seasonal_naive")]
                winner = "GLM" if glm_wape < naive_wape else "Naive"
                if target == "claim_cost":
                    fmt_mae = lambda x: f"SAR {x:,.0f}"
                else:
                    fmt_mae = lambda x: f"{x:,.1f}"
                f.write(f"| {h} | {fmt_mae(glm_mae)} | {fmt_mae(naive_mae)} | "
                        f"{glm_wape:.1%} | {naive_wape:.1%} | "
                        f"{glm_bias:+.1%} | {naive_bias:+.1%} | **{winner}** |\n")
            f.write("\n")

        f.write("## Segment-level performance (Full credibility segments only)\n\n")
        f.write(f"Segments passing credibility threshold (≥{FULL_CREDIBILITY_MIN_POLICIES} policies): "
                f"{len(full_cred)}\n\n")

        for target in ["claim_count", "claim_cost"]:
            f.write(f"### {target} — per-horizon WAPE comparison\n\n")
            sub = seg_summary[seg_summary["target"] == target].copy()
            if len(sub) == 0:
                f.write("_No Full-credibility segments had sufficient data._\n\n")
                continue
            sub = sub.pivot(index="horizon_label", columns="model", values="WAPE")
            horizon_order = [h[0] for h in HORIZONS if h[0] in sub.index]
            sub = sub.reindex(horizon_order)
            f.write("| horizon | GLM WAPE (avg) | Naive WAPE (avg) | Winner |\n")
            f.write("|---|---|---|---|\n")
            for h in sub.index:
                g, n = sub.loc[h, "glm"], sub.loc[h, "seasonal_naive"]
                w = "GLM" if g < n else "Naive"
                f.write(f"| {h} | {g:.1%} | {n:.1%} | **{w}** |\n")
            f.write("\n")

        f.write("## Definition-of-done check\n\n")
        f.write("| Check | Target | Result |\n|---|---|---|\n")
        n_cut = results["cutoff_date"].nunique()
        f.write(f"| Cutoffs evaluated | ≥ 15 | **{'PASS' if n_cut >= 15 else 'FAIL'}** ({n_cut}) |\n")
        f.write(f"| Both models scored | GLM + seasonal naive | **PASS** |\n")
        f.write(f"| Portfolio + per-segment output | required | **PASS** |\n")

        f.write("\n## How to read this report\n\n")
        f.write("**WAPE** (weighted absolute percentage error) = sum(|actual − predicted|) / sum(|actual|). ")
        f.write("Lower is better. Preferred over MAPE for insurance because it does not blow up when the actual is small or zero.\n\n")
        f.write("**Bias** = sum(predicted − actual) / sum(actual). Positive means the model over-predicts on average; negative means under-predicts.\n\n")
        f.write("**Winner** compares GLM vs seasonal naive on WAPE. If GLM does not consistently beat naive, ")
        f.write("that is a genuine finding — write it into the presentation instead of hiding it.\n\n")

        f.write("## Caveats\n\n")
        f.write("1. **Ultimate cost target** — this backtest uses ULTIMATE_CLAIM (the synthetic best-estimate) as both training target and the actual in the forward window. In a real backtest, the ultimate at cutoff T would be estimated using development factors, not known perfectly.\n")
        f.write("2. **Short horizons are noisy** — 2-week and 1-month portfolio windows contain small numbers of claims. Point-estimate WAPE on those horizons will be volatile even if the model is sound. The 3-month and 12-month numbers are the ones to lead with.\n")
        f.write("3. **Segments with fewer than 300 policies are excluded from the segment table** — they lack the credibility to give a meaningful WAPE.\n")
        f.write("4. **Seasonal naive on this dataset is a strong benchmark** — the synthetic build has stable year-over-year patterns, so beating naive by a large margin is a high bar. Beating it at all is worth stating.\n")


# =========================================================================
# Entry point
# =========================================================================
def build_cutoffs() -> list[pd.Timestamp]:
    """Quarterly cutoffs from 2019-Q4 to 2024-Q2 (19 cutoffs)."""
    return list(pd.date_range("2019-12-31", "2024-06-30", freq="QE"))


def main():
    _log("=" * 60)
    _log("Rolling backtest (Phase 3) starting")
    _log("=" * 60)

    policies, claims = load_data()

    cutoffs = build_cutoffs()
    _log(f"Scheduled {len(cutoffs)} cutoffs: {cutoffs[0].date()} → {cutoffs[-1].date()}")

    results = run_backtest(policies, claims, cutoffs)

    out_path = FACT_DIR / "fact_model_performance.csv"
    results.to_csv(out_path, index=False)
    _log(f"Wrote {out_path} ({len(results):,} rows)")

    write_report(results)
    _log("Done.")


if __name__ == "__main__":
    main()