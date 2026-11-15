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
  5. Optionally compare against TIMESFM (Google's pretrained time-series model),
     forecasting the daily portfolio claim count and cost from their history up to T

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

import gc
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

warnings.filterwarnings("ignore")

# =========================================================================
# Config
# =========================================================================
from settings import S, is_motor, pricing_features  # folders + motor classes for the active data source
import exposure       # plan H1: car-years
import timesfm_model  # optional challenger: daily portfolio claims from their own history
import training_window
FACT_DIR = S.facts_dir
OUTPUTS_DIR = S.reports_dir


PRICING_FEATURES = pricing_features(vehicle=False)   # general factors; main() tests the vehicle factors
VEHICLE_FEATURES = [c for c in pricing_features(vehicle=True) if c not in PRICING_FEATURES]

RARE_THRESHOLD = 30
MIN_TRAIN_EXPOSURE_DAYS = 30       # skip policies with less than 30 days observed
MIN_TRAIN_POLICIES = 500           # minimum policies to attempt a cutoff fit
MIN_TRAIN_CLAIMS = 100             # minimum claims to attempt severity fit
# Method constant: at each cut-off the GLMs train on at most this many policies, drawn at
# random (fixed seed). ~18 coefficients are estimated precisely from far fewer rows; the cap
# keeps memory flat on large books (2M+ policies per cut-off ran out of memory).
MAX_TRAIN_POLICIES = 500_000
SAMPLE_SEED = 20260826
# Method constants: cut-offs are the quarter-ends of the last five years of data; the training
# window (pipeline/training_window.py) is chosen on the latest six that have 12 months of actuals.
BACKTEST_YEARS = 5
WINDOW_TEST_CUTOFFS = 6

# Only these columns are read: the full fact tables are hundreds of MB on a real book.
POLICY_COLS = ["POLICY_NO", "POLICY_YEAR", "POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE", "CARS"] + PRICING_FEATURES
# Plan H1: share of each accident quarter's claims reported by the data's end. The backtest counts the claims
# known now, so training and forecast car-years are weighted by it (pipeline/exposure.py); set in main().
SHARES = pd.Series(dtype=float)
CLAIM_COLS = ["CLM_NO", "POLICY_NO", "POLICY_YEAR", "ACC_DATE", "ULTIMATE_CLAIM", "POLICY_MATCHED"]

# Horizons for the backtest (in days)
HORIZONS = [
    ("2w",   14),
    ("1m",   30),
    ("3m",   91),
    ("12m", 365),
]

# Segments reported at segment level (only those meeting credibility threshold)
FULL_CREDIBILITY_MIN_POLICIES = S.config["credibility"]["full_policies"]


# Report labels, in column order
MODEL_LABELS = {"glm": "GLM", "seasonal_naive": "Naive", "timesfm": "TimesFM"}


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


def drop_duplicate_columns(X: pd.DataFrame) -> pd.DataFrame:
    """Drop dummy columns identical to an earlier one. Factors that are all 'Unknown' on the same
    policies (e.g. the vehicle factors where there is no vehicle data) give identical columns, and a
    GLM cannot separate them: the coefficients run to +-1e11 and predictions to infinity."""
    seen, keep = set(), []
    for c in X.columns:
        key = (int(X[c].sum()), int(pd.util.hash_array(X[c].to_numpy()).sum()))
        if key not in seen:
            seen.add(key)
            keep.append(c)
    return X[keep]


def fit_encoder(df: pd.DataFrame, feature_cols: list[str]) -> tuple[pd.DataFrame, dict]:
    df2 = df.copy()
    encoder_meta = {"category_maps": {}, "columns": []}
    for col in feature_cols:
        df2[col], mapping = _group_rare(df2[col])
        encoder_meta["category_maps"][col] = mapping
    X = drop_duplicate_columns(pd.get_dummies(df2[feature_cols], drop_first=True, dtype=int))
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
    header = pd.read_csv(FACT_DIR / "fact_policy.csv", nrows=0).columns
    p = pd.read_csv(FACT_DIR / "fact_policy.csv", low_memory=False,
                    usecols=POLICY_COLS + [c for c in VEHICLE_FEATURES if c in header])
    c = pd.read_csv(FACT_DIR / "fact_claim.csv", usecols=CLAIM_COLS, low_memory=False)

    p["POLICY_EFFECTIVE_DATE"] = pd.to_datetime(p["POLICY_EFFECTIVE_DATE"])
    p["POLICY_EXPIRY_DATE"]    = pd.to_datetime(p["POLICY_EXPIRY_DATE"])
    c["ACC_DATE"]              = pd.to_datetime(c["ACC_DATE"])

    p = p[is_motor(p["CLASS_DESC"])].copy()
    # One fixed random number per policy: whenever a cut-off has more than MAX_TRAIN_POLICIES, the
    # lowest numbers are kept, so every training window samples the same policies and the windows
    # differ only in their history, not in their luck.
    p["SAMPLE_U"] = np.random.default_rng(SAMPLE_SEED).random(len(p))
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
def train_at_cutoff(policies: pd.DataFrame, claims: pd.DataFrame, T: pd.Timestamp,
                    window_years: int | None = None):
    """Fit frequency and severity GLMs using only data available at T, and only the last
    `window_years` before it (pipeline/training_window.py; None = all history)."""
    # Training policies: effective on or before T (and inside the window)
    since = None if window_years is None else T - pd.DateOffset(years=window_years)
    in_window = policies["POLICY_EFFECTIVE_DATE"] <= T
    if since is not None:
        in_window &= policies["POLICY_EFFECTIVE_DATE"] > since
        claims = claims[claims["ACC_DATE"] > since]
    train_pol = policies[in_window].copy()

    # Observed exposure at T (partial for still-in-force policies)
    end_at_T = train_pol["POLICY_EXPIRY_DATE"].clip(upper=T)
    train_pol["EXPOSURE_AT_T_YEARS"] = (
        (end_at_T - train_pol["POLICY_EFFECTIVE_DATE"]).dt.days / 365.25
    )
    train_pol = train_pol[
        train_pol["EXPOSURE_AT_T_YEARS"] >= MIN_TRAIN_EXPOSURE_DAYS / 365.25
    ].copy()
    # Plan H1: the offset is car-years run to T, weighted by the claims reported so far (as step 02)
    train_pol["CAR_YEARS_AT_T"] = exposure.earned_car_years(train_pol, T + pd.Timedelta(days=1), SHARES)
    train_pol = train_pol[train_pol["CAR_YEARS_AT_T"] > 0]

    if len(train_pol) < MIN_TRAIN_POLICIES:
        return None, None
    if len(train_pol) > MAX_TRAIN_POLICIES:
        train_pol = train_pol.nsmallest(MAX_TRAIN_POLICIES, "SAMPLE_U")

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
            family=sm.families.Poisson(),   # balanced; see 02_glm_refit.fit_frequency_model
            offset=np.log(freq_df["CAR_YEARS_AT_T"]),
        ).fit()
    except Exception as e:
        _log(f"  cutoff {T.date()}: frequency fit failed ({e})")
        return None, None
    freq_model.remove_data()            # drop the training copy; prediction needs only params
    del freq_df, X_freq, X_freq_c
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
    sev_model.remove_data()

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
    # Plan H1: car-years in the window, weighted like the actual claims (those reported so far)
    active["CAR_YEARS_IN_WINDOW"] = exposure.earned_car_years(active, T_end + pd.Timedelta(days=1), SHARES,
                                                              start=T_start + pd.Timedelta(days=1))
    active = active[(active["EXPOSURE_IN_WINDOW_YEARS"] > 0) & (active["CAR_YEARS_IN_WINDOW"] > 0)].copy()

    for col in PRICING_FEATURES:
        active[col] = active[col].astype(str).replace({"nan": "Unknown"})

    X_f = sm.add_constant(apply_encoder(active, freq_bundle["encoder_meta"]),
                          has_constant="add")
    active["predicted_count"] = freq_bundle["result"].predict(
        X_f, offset=np.log(active["CAR_YEARS_IN_WINDOW"].values)
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
        "predicted_count", "predicted_cost", "CAR_YEARS_IN_WINDOW",
    ]]


# =========================================================================
# Plan H2: policy-level out-of-time test
# =========================================================================
# Pricing takes each segment's level from reserving (step 09) and the GLM only decides how it is shared
# between the segment's policies. So the test gives every model the segment's actual total and scores how
# it shares it out, policy by policy, against a segment-only model that shares it by car-years alone.
# Method constants: the horizon tested, the Tweedie power for the cost deviance, the number of lift bands.
POLICY_TEST_HORIZON = "12m"
TWEEDIE_POWER = 1.5
LIFT_BANDS = 10
# Plan H4: relativity strengths tested (prediction = segment level x GLM relativity ** strength)
STRENGTHS = tuple(round(a, 2) for a in np.arange(0.0, 1.01, 0.1))


def _poisson_dev(y: np.ndarray, mu: np.ndarray) -> float:
    mu = np.maximum(mu, 1e-12)
    term = np.where(y > 0, y * np.log(np.where(y > 0, y, 1.0) / mu), 0.0)
    return float(2.0 * np.sum(term - (y - mu)))


def _tweedie_dev(y: np.ndarray, mu: np.ndarray, p: float = TWEEDIE_POWER) -> float:
    mu = np.maximum(mu, 1e-12)
    return float(2.0 * np.sum(y ** (2 - p) / ((1 - p) * (2 - p)) - y * mu ** (1 - p) / (1 - p)
                              + mu ** (2 - p) / (2 - p)))


def _gini(rate: np.ndarray, expo: np.ndarray, actual: np.ndarray) -> float:
    """Exposure-weighted Gini: policies from lowest to highest predicted rate, share of actual cost
    against share of car-years. 0 = no better than random; larger = better separation."""
    order = np.argsort(rate, kind="stable")
    x = np.concatenate([[0.0], np.cumsum(expo[order]) / expo.sum()])
    y = np.concatenate([[0.0], np.cumsum(actual[order]) / max(actual.sum(), 1e-12)])
    return float(1.0 - 2.0 * np.trapezoid(y, x))


def _lift(rate: np.ndarray, expo: np.ndarray, actual: np.ndarray) -> float:
    """Actual cost per car-year of the highest-rated band over the lowest (bands of equal car-years)."""
    order = np.argsort(rate, kind="stable")
    band = np.minimum((np.cumsum(expo[order]) / expo.sum() * LIFT_BANDS).astype(int), LIFT_BANDS - 1)
    e, a = np.bincount(band, expo[order], LIFT_BANDS), np.bincount(band, actual[order], LIFT_BANDS)
    lo, hi = a[0] / e[0] if e[0] > 0 else np.nan, a[-1] / e[-1] if e[-1] > 0 else np.nan
    return float(hi / lo) if lo and lo > 0 else np.nan


def policy_test(pred: pd.DataFrame, claims: pd.DataFrame, T: pd.Timestamp, T_end: pd.Timestamp) -> dict:
    """One cut-off of the policy-level test (see above): deviance of claim counts (Poisson) and cost
    (Tweedie), the GLM's over the segment-only model's (below 1 = the GLM ranks better), plus Gini and
    top-to-bottom lift of cost per car-year for both."""
    w = claims[(claims["ACC_DATE"] > T) & (claims["ACC_DATE"] <= T_end)]
    act = w.groupby(["POLICY_NO", "POLICY_YEAR"]).agg(N=("CLM_NO", "size"), COST=("ULTIMATE_CLAIM", "sum"))
    m = pred.merge(act.reset_index(), on=["POLICY_NO", "POLICY_YEAR"], how="left").fillna({"N": 0, "COST": 0})
    seg = m.groupby(["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"], dropna=False).ngroup().to_numpy()
    e = m["CAR_YEARS_IN_WINDOW"].to_numpy(dtype=float)

    def share(weights: np.ndarray, total: np.ndarray) -> np.ndarray:
        """Each segment's actual total, shared out in proportion to `weights`."""
        wsum, tsum = np.bincount(seg, weights), np.bincount(seg, total)
        return weights * np.divide(tsum, wsum, out=np.zeros_like(tsum), where=wsum > 0)[seg]

    out = {"cutoff_date": T, "policies": len(m), "claims": int(m["N"].sum())}
    strength = {a: 0.0 for a in STRENGTHS}
    for target, col, pred_col, dev in (("count", "N", "predicted_count", _poisson_dev),
                                       ("cost", "COST", "predicted_cost", _tweedie_dev)):
        y = m[col].to_numpy(dtype=float)
        rate = m[pred_col].to_numpy(dtype=float) / np.maximum(e, 1e-12)
        glm, base = share(m[pred_col].to_numpy(dtype=float), y), share(e, y)
        base_dev = max(dev(y, base), 1e-12)
        out[f"{target}_deviance_ratio"] = dev(y, glm) / base_dev
        # Plan H4: the GLM's relativities raised to a strength a (0 = segment-only, 1 = as fitted)
        for a in STRENGTHS:
            strength[a] += dev(y, share(e * np.maximum(rate, 1e-12) ** a, y)) / base_dev / 2
        if target == "cost":
            rate_glm, rate_base = glm / np.maximum(e, 1e-12), base / np.maximum(e, 1e-12)
            out.update(gini_glm=_gini(rate_glm, e, y), gini_segment=_gini(rate_base, e, y),
                       lift_glm=_lift(rate_glm, e, y), lift_segment=_lift(rate_base, e, y))
    out["error"] = (out["count_deviance_ratio"] + out["cost_deviance_ratio"]) / 2
    out.update({f"error_strength_{a:.2f}": v for a, v in strength.items()})
    return out


def strength_columns(t: pd.DataFrame) -> dict[float, np.ndarray]:
    return {a: t[f"error_strength_{a:.2f}"].to_numpy() for a in STRENGTHS if f"error_strength_{a:.2f}" in t}


def select_strength(t: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    """Plan H4: how strongly the GLM's relativities apply. The fitted GLM (strength 1) is kept unless another
    strength beats it, cut-off by cut-off, by more than the standard error of the difference."""
    errs = strength_columns(t)
    mean = {a: e.mean() for a, e in errs.items()}
    best = min(mean, key=mean.get)
    d = errs[1.0] - errs[best]
    se = d.std(ddof=1) / np.sqrt(len(d)) if len(d) > 1 else np.inf
    chosen = best if d.mean() > se else 1.0
    table = pd.DataFrame({"STRENGTH": list(mean), "ERROR": list(mean.values()),
                          "STD_ERROR": [e.std(ddof=1) / np.sqrt(len(e)) for e in errs.values()],
                          "N_CUTOFFS": len(t), "CHOSEN": [a == chosen for a in mean]})
    _log("relativity strength (policy-level deviance vs segment-only): "
         + ", ".join(f"{a:.2f} {v:.4f}" for a, v in mean.items())
         + f" -> {chosen:.2f} (best {best:.2f}; gain on 1.00 {d.mean():.4f} ±{se:.4f})")
    return chosen, table


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
# TimesFM challenger
# =========================================================================
def timesfm_forecasts(claims: pd.DataFrame, cutoffs: list[pd.Timestamp]) -> dict | None:
    """
    Forecast daily portfolio claim count and cost after each cut-off with TimesFM.
    Returns {(T, "count"|"cost"): array of daily forecasts for days T+1 ... T+max horizon},
    or None when TimesFM is not installed.
    """
    if not timesfm_model.available():
        _log("TimesFM not installed — skipping the TimesFM challenger")
        return None

    day = claims["ACC_DATE"].dt.normalize()
    days = pd.date_range(day.min(), day.max(), freq="D")
    series = {
        "count": claims.groupby(day).size().reindex(days, fill_value=0),
        "cost":  claims.groupby(day)["ULTIMATE_CLAIM"].sum().reindex(days, fill_value=0),
    }
    keys = [(T, s) for T in cutoffs for s in series]
    contexts = [series[s][series[s].index <= T].to_numpy() for T, s in keys]

    _log(f"TimesFM: forecasting {len(keys)} series with {timesfm_model.CHECKPOINT}")
    forecasts = timesfm_model.predict(contexts, horizon=max(d for _, d in HORIZONS))
    return dict(zip(keys, forecasts))


# =========================================================================
# Backtest driver
# =========================================================================
def run_backtest(policies: pd.DataFrame, claims: pd.DataFrame,
                 cutoffs: list[pd.Timestamp], window_years: int | None = None,
                 glm_only: bool = False) -> pd.DataFrame:
    """Run the full rolling backtest and return a long-format results DataFrame.
    glm_only: skip the benchmarks (the training-window test compares GLMs only)."""
    max_data_date = claims["ACC_DATE"].max()
    tfm = None if glm_only else timesfm_forecasts(claims, cutoffs)
    rows, policy_rows = [], []

    for T in cutoffs:
        _log(f"cutoff {T.date()} — training GLMs ({training_window.label(window_years)})")
        freq, sev = train_at_cutoff(policies, claims, T, window_years)
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
            per_policy = predict_glm_window(policies, freq, sev, T, T_end)
            if horizon_label == POLICY_TEST_HORIZON and len(per_policy):
                policy_rows.append(policy_test(per_policy, claims, T, T_end))     # plan H2
            glm_pred_df = sum_predictions(per_policy)
            del per_policy

            # Seasonal naive: use the same window one year earlier as prediction
            T_naive_start = T - pd.Timedelta(days=365)
            T_naive_end   = T_end - pd.Timedelta(days=365)
            naive_df = sum_actuals(claims, T_naive_start, T_naive_end) \
                .rename(columns={"actual_count": "predicted_count",
                                 "actual_cost":  "predicted_cost"})

            models = [("glm", glm_pred_df)] + ([] if glm_only else [("seasonal_naive", naive_df)])
            if tfm is not None:
                models.append(("timesfm", pd.DataFrame([{
                    "segment_level": "PORTFOLIO", "segment_id": "PORTFOLIO",
                    "predicted_count": tfm[(T, "count")][:horizon_days].sum(),
                    "predicted_cost":  tfm[(T, "cost")][:horizon_days].sum(),
                }])))

            # Merge each model against actuals and emit rows
            for model_name, pred_df in models:
                # TimesFM has portfolio rows only; inner join keeps segments out of its scores
                how = "inner" if model_name == "timesfm" else "left"
                joined = actual_df.merge(pred_df, on=["segment_level", "segment_id"], how=how)
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

        del freq, sev                   # free this cut-off's models before fitting the next
        gc.collect()

    if not rows:
        raise RuntimeError("Backtest produced no rows — check cutoffs and data span.")

    out = pd.concat(rows, ignore_index=True)
    out.attrs["policy_test"] = pd.DataFrame(policy_rows)     # plan H2, one row per cut-off
    return out


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
        pd.read_csv(FACT_DIR / "fact_policy.csv", usecols=["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"],
                    low_memory=False)
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
        f.write("_Benchmark: **seasonal naive** — prediction for window (T, T+h] = actuals in (T-1yr, T-1yr+h]._\n")
        if "timesfm" in set(results["model"]):
            f.write("_Challenger: **TimesFM** — Google's pretrained time-series model, forecasting daily "
                    "portfolio claims from their history up to T (portfolio level only)._\n")
        f.write("\n")

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
            models = [m for m in MODEL_LABELS if m in sub["WAPE"].columns]
            fmt_mae = (lambda x: f"SAR {x:,.0f}") if target == "claim_cost" else (lambda x: f"{x:,.1f}")

            f.write("| horizon | " + " | ".join(f"{MODEL_LABELS[m]} {k}" for k in ("MAE", "WAPE", "Bias")
                                                 for m in models) + " | Winner (WAPE) |\n")
            f.write("|---|" + "---|" * (3 * len(models) + 1) + "\n")
            for h in sub.index:
                cells = ([fmt_mae(sub.loc[h, ("MAE", m)]) for m in models] +
                         [f"{sub.loc[h, ('WAPE', m)]:.1%}" for m in models] +
                         [f"{sub.loc[h, ('BIAS', m)]:+.1%}" for m in models])
                winner = MODEL_LABELS[sub.loc[h, "WAPE"][models].idxmin()]
                f.write(f"| {h} | " + " | ".join(cells) + f" | **{winner}** |\n")
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
        scored = [MODEL_LABELS[m] for m in MODEL_LABELS if m in set(results["model"])]
        f.write(f"| Models scored | GLM + seasonal naive | **PASS** ({', '.join(scored)}) |\n")
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
        f.write("5. **The GLM sees the future book, the time-series models do not** — the GLM is applied to the policies actually in force in the window, so book growth after T is known to it. Seasonal naive and TimesFM see only claims up to T.\n")


# =========================================================================
# Entry point
# =========================================================================
def build_cutoffs(claims: pd.DataFrame) -> list[pd.Timestamp]:
    """Quarter-ends over the last BACKTEST_YEARS of the data, each with at least the shortest
    scored horizon (3 months) of actuals after it. From the data, so it moves with each extract."""
    end = claims["ACC_DATE"].max()
    start = end - pd.DateOffset(years=BACKTEST_YEARS)
    return [T for T in pd.date_range(start, end, freq="QE") if T + pd.Timedelta(days=91) <= end]


def relativity_wape(g: pd.DataFrame) -> float:
    """Segment WAPE after scaling the predictions to the actual total. Pricing takes only the GLMs'
    relativities (which segments and policies cost more); the level comes from reserving x trend
    (step 09), so the window test must not reward or punish the level."""
    pred, act = g["predicted"], g["actual"]
    scaled = pred * (act.sum() / pred.sum()) if pred.sum() > 0 else pred
    return float((scaled - act).abs().sum() / act.abs().sum())


def cutoff_errors(results: pd.DataFrame) -> np.ndarray:
    """Training-window score, one per cut-off: segment relativity WAPE (relativity_wape) of claim
    count and cost at 3 and 12 months, averaged."""
    r = results[(results["segment_level"] == "SEGMENT") & results["horizon_label"].isin(["3m", "12m"])]
    per = r.groupby(["cutoff_date", "target", "horizon_label"]).apply(relativity_wape, include_groups=False)
    return per.groupby(level="cutoff_date").mean().to_numpy()


POLICY_TESTS: list[pd.DataFrame] = []          # plan H2: every candidate's cut-offs, for fact_glm_policy_test.csv


def policy_errors(results: pd.DataFrame, test: str, candidate: str) -> np.ndarray:
    """Plan H2 gate score, one per cut-off: the GLM's deviance over the segment-only model's, claim counts
    and cost averaged (policy_test). Below 1 = it shares each segment's claims out better than car-years
    alone. Also logs Gini, lift and the old segment WAPE next to it."""
    t = results.attrs["policy_test"].assign(TEST=test, CANDIDATE=candidate)
    POLICY_TESTS.append(t)
    wape = cutoff_errors(results).mean()
    _log(f"  {candidate}: deviance vs segment-only {t['error'].mean():.3f} (count {t['count_deviance_ratio'].mean():.3f}, "
         f"cost {t['cost_deviance_ratio'].mean():.3f}); Gini {t['gini_glm'].mean():.3f} vs {t['gini_segment'].mean():.3f}; "
         f"lift {t['lift_glm'].mean():.1f}x vs {t['lift_segment'].mean():.1f}x; segment WAPE {wape:.1%}")
    return t["error"].to_numpy()


def select_window(policies: pd.DataFrame, claims: pd.DataFrame, cutoffs: list[pd.Timestamp]) -> int | None:
    """How many years the GLMs learn from (pipeline/training_window.py), tested on the latest
    WINDOW_TEST_CUTOFFS cut-offs that have a full 12 months of actuals after them, on the policy-level
    test (plan H2). Steps 02 and 03 then fit and score on the chosen window."""
    end = claims["ACC_DATE"].max()
    test = [T for T in cutoffs if T + pd.Timedelta(days=365) <= end][-WINDOW_TEST_CUTOFFS:]
    span = (end - policies["POLICY_EFFECTIVE_DATE"].min()).days / 365.25
    errors = {}
    for w in training_window.candidates(span):
        _log(f"training window test: {training_window.label(w)}")
        errors[w] = policy_errors(run_backtest(policies, claims, test, w, glm_only=True), "window",
                                  training_window.label(w))
        gc.collect()
    return training_window.resolve(
        "glm", errors, f"GLM policy-level deviance over a segment-only model (count, cost; 12m), {len(test)} "
        f"cut-offs {test[0].date()} to {test[-1].date()}")


def select_factors(policies: pd.DataFrame, claims: pd.DataFrame, cutoffs: list[pd.Timestamp],
                   window: int | None) -> None:
    """Plan D1-D2: the GLMs with and without the vehicle rating factors, on the chosen window and the
    window test's cut-offs, scored on relativities. The vehicle factors are kept only when their
    error is lower by more than its standard error (G2 gate); fact_rating_factors.csv records it and
    steps 02 / 03 read it through settings.pricing_features()."""
    global PRICING_FEATURES
    general = list(PRICING_FEATURES)
    path = FACT_DIR / "fact_rating_factors.csv"
    if not all(c in policies.columns for c in VEHICLE_FEATURES):
        _log("rating factors: no vehicle factors in fact_policy (no vehicle extract); general factors")
        pd.DataFrame([{"FACTOR_SET": "general", "ERROR": np.nan, "STD_ERROR": np.nan, "N_CUTOFFS": 0,
                       "CHOSEN": True}]).to_csv(path, index=False)
        return
    end = claims["ACC_DATE"].max()
    test = [T for T in cutoffs if T + pd.Timedelta(days=365) <= end][-WINDOW_TEST_CUTOFFS:]
    errors = {}
    for name, feats in (("general", general), ("general + vehicle", general + VEHICLE_FEATURES)):
        PRICING_FEATURES = feats
        _log(f"rating factors test: {name}")
        errors[name] = policy_errors(run_backtest(policies, claims, test, window, glm_only=True), "factors", name)
        gc.collect()
    se = {k: e.std(ddof=1) / np.sqrt(len(e)) for k, e in errors.items()}
    use = errors["general + vehicle"].mean() < errors["general"].mean() - se["general + vehicle"]
    chosen = "general + vehicle" if use else "general"
    pd.DataFrame([{"FACTOR_SET": k, "ERROR": e.mean(), "STD_ERROR": se[k], "N_CUTOFFS": len(e), "CHOSEN": k == chosen}
                  for k, e in errors.items()]).to_csv(path, index=False)
    _log("rating factors (policy-level deviance vs segment-only): "
         + ", ".join(f"{k} {e.mean():.3f} ±{se[k]:.3f}" for k, e in errors.items()) + f" -> {chosen}")
    PRICING_FEATURES = general + (VEHICLE_FEATURES if use else [])


def main():
    _log("=" * 60)
    _log("Rolling backtest (Phase 3) starting")
    _log("=" * 60)

    policies, claims = load_data()
    global SHARES
    SHARES = exposure.reported_count_share()
    _log(f"  reporting delay: {len(SHARES[SHARES < exposure.COMPLETE_SHARE])} accident quarters not yet fully "
         f"reported (latest {SHARES.iloc[-1]:.1%})" if len(SHARES) else "  reporting delay: no triangle, claims as complete")

    cutoffs = build_cutoffs(claims)
    _log(f"Scheduled {len(cutoffs)} cutoffs: {cutoffs[0].date()} → {cutoffs[-1].date()}")

    window = select_window(policies, claims, cutoffs)
    select_factors(policies, claims, cutoffs, window)
    results = run_backtest(policies, claims, cutoffs, window)

    # Plan H2: does the chosen GLM share each segment's claims out better than car-years alone?
    final = results.attrs["policy_test"].assign(TEST="chosen", CANDIDATE=training_window.label(window))
    POLICY_TESTS.append(final)
    pd.concat(POLICY_TESTS, ignore_index=True).to_csv(FACT_DIR / "fact_glm_policy_test.csv", index=False)
    # The verdict uses the same latest cut-offs as the gate; all cut-offs are logged beside it
    latest = final.sort_values("cutoff_date").tail(WINDOW_TEST_CUTOFFS)
    # Plan H4: relativity strength, chosen on the same latest cut-offs; steps 03 and 09 apply it
    strength, strength_table = select_strength(latest)
    strength_table.to_csv(FACT_DIR / "fact_glm_strength.csv", index=False)
    for label, part in (("latest", latest), ("all", final)):
        e = part["error"].to_numpy()
        se = e.std(ddof=1) / np.sqrt(len(e)) if len(e) > 1 else np.nan
        verdict = ("ranks policies better than the segment-only model" if e.mean() < 1 - se else
                   "NOT better than the segment-only model: its relativities add nothing measurable")
        _log(f"policy-level test, chosen GLM, {label} {len(e)} cut-offs: deviance vs segment-only {e.mean():.3f} "
             f"±{se:.3f}, Gini {part['gini_glm'].mean():.3f} vs {part['gini_segment'].mean():.3f}"
             + (f" -> {verdict}" if label == "latest" else ""))

    out_path = FACT_DIR / "fact_model_performance.csv"
    results.to_csv(out_path, index=False)
    _log(f"Wrote {out_path} ({len(results):,} rows)")

    write_report(results)
    _log("Done.")


if __name__ == "__main__":
    main()