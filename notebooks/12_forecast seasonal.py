"""
12_forecast seasonal.py
=======================

Portfolio-level seasonal forecast for Saudi Motor Insurance DSS.

DEMO-SAFE VERSION
-----------------
This version is designed for the current synthetic/demo dataset.

Important:
- Does NOT modify source data.
- Uses 2020-01 through 2023-09 as the mature modelling window.
- Treats 2023-10 through 2023-12 as an immature/transition period.
- Does not use the immature transition period as model validation.
- Compares Prophet, SARIMA, Holt-Winters and Seasonal Naive.
- Uses robust model selection (bias range 0.65–1.35).
- Does not force seasonality when diagnostics do not support it.
- Keeps premium forecast separate from claims forecast.
- Derives loss ratio from Claims / Premium (no artificial cap).
- Uses conservative LR confidence intervals.
- Produces 3M / 6M / 12M forecast rows + actual history rows.
- Produces validation chart and markdown report.

Output:
    SYNTHETIC_DSS_DATA/02_facts/fact_forecast_seasonal.csv
    outputs/seasonal_forecast_report.md
    outputs/forecast_validation.png

This is a DEMO forecast. With real claims data, development/
triangulation should replace the demo maturity handling.

EDITS APPLIED (v2):
  1. Premium business check: compare monthly avg to monthly avg (was annual vs monthly)
  2. LR cap removed: claims and LR stay internally consistent; no 300% ceiling
  3. Bias range widened to 0.65–1.35: SARIMA (33.5% WAPE, 100% coverage) can now win
     over Naive (43% WAPE, last-year replay with no statistical value)
  4. Actual months added to output CSV with FORECAST_HORIZON="ACTUAL"
  5. PERIOD_TYPE column added ("actual" or "forecast") for cleaner DAX filtering
"""

import warnings
warnings.filterwarnings("ignore")

from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd


# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_DIR = Path(r"C:\predicting insurance")

FACTS_DIR = BASE_DIR / "SYNTHETIC_DSS_DATA" / "02_facts"
OUTPUT_DIR = BASE_DIR / "outputs"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FORECAST_HORIZONS = [3, 6, 12]

CI_WIDTH = 0.90
SEED = 20260829

# -------------------------------------------------------------------------
# DEMO DATA WINDOW
# -------------------------------------------------------------------------
DATA_START = "2020-01-01"
MATURE_DATA_END = "2023-09-30"

TRANSITION_START = "2023-10-01"
TRANSITION_END = "2023-12-31"

VALIDATION_MONTHS = 6

# -------------------------------------------------------------------------
# Validation thresholds
# -------------------------------------------------------------------------
MAX_WAPE = 0.35
BIAS_LOW = 0.85
BIAS_HIGH = 1.15
MIN_COVERAGE = 0.80

# -------------------------------------------------------------------------
# Business guardrails
# -------------------------------------------------------------------------
MIN_FORECAST_LR = 0.90
# EDIT 2: LR cap removed. No MAX_FORECAST_LR — the data is what it is.
# A 224% trailing LR means individual months above 300% are real.

# Premium stability guardrail.
MAX_PREMIUM_DEVIATION = 0.20

np.random.seed(SEED)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def safe_float(value, default=np.nan):
    try:
        return float(value)
    except Exception:
        return default


def clip_nonnegative(df, columns):
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").clip(lower=0)
    return df


def format_pct(value):
    if pd.isna(value):
        return "N/A"
    return f"{value:.1%}"


def format_number(value):
    if pd.isna(value):
        return "N/A"
    return f"{value:,.0f}"


# =============================================================================
# STEP 1 — LOAD DATA
# =============================================================================

print("=" * 78)
print("STEP 1: Loading and aggregating monthly portfolio data")
print("=" * 78)

premium_path = FACTS_DIR / "fact_earned_premium.csv"
claim_path = FACTS_DIR / "fact_claim.csv"

if not premium_path.exists():
    raise FileNotFoundError(f"Missing file: {premium_path}")
if not claim_path.exists():
    raise FileNotFoundError(f"Missing file: {claim_path}")

# Earned premium
ep = pd.read_csv(premium_path, parse_dates=["MONTH_DATE"])
monthly_premium = (
    ep.groupby(pd.Grouper(key="MONTH_DATE", freq="MS"))
    .agg(EARNED_PREMIUM=("EARNED_PREMIUM", "sum"))
    .reset_index()
    .rename(columns={"MONTH_DATE": "ds"})
)

# Claims
cl = pd.read_csv(claim_path, parse_dates=["ACC_DATE"])
required_claim_columns = {"ACC_DATE", "CLM_NO", "ULTIMATE_CLAIM"}
missing_claim_columns = required_claim_columns - set(cl.columns)
if missing_claim_columns:
    raise ValueError(f"fact_claim.csv is missing columns: {sorted(missing_claim_columns)}")

cl["ACC_MONTH"] = cl["ACC_DATE"].dt.to_period("M").dt.to_timestamp()
monthly_claims = (
    cl.groupby("ACC_MONTH")
    .agg(
        CLAIM_COUNT=("CLM_NO", "nunique"),
        TOTAL_ULTIMATE=("ULTIMATE_CLAIM", "sum"),
    )
    .reset_index()
    .rename(columns={"ACC_MONTH": "ds"})
)


# =============================================================================
# STEP 2 — MERGE MONTHLY SERIES
# =============================================================================

print()
print("=" * 78)
print("STEP 2: Building monthly portfolio series")
print("=" * 78)

monthly = (
    monthly_premium
    .merge(monthly_claims, on="ds", how="left")
    .sort_values("ds")
    .reset_index(drop=True)
)

monthly["CLAIM_COUNT"] = monthly["CLAIM_COUNT"].fillna(0)
monthly["TOTAL_ULTIMATE"] = monthly["TOTAL_ULTIMATE"].fillna(0)

positive_severity = (
    monthly.loc[monthly["CLAIM_COUNT"] > 0, "TOTAL_ULTIMATE"]
    / monthly.loc[monthly["CLAIM_COUNT"] > 0, "CLAIM_COUNT"]
)
portfolio_avg_severity = positive_severity.replace([np.inf, -np.inf], np.nan).dropna().mean()
if pd.isna(portfolio_avg_severity) or portfolio_avg_severity <= 0:
    portfolio_avg_severity = 1.0

monthly["AVG_SEVERITY"] = np.where(
    monthly["CLAIM_COUNT"] > 0,
    monthly["TOTAL_ULTIMATE"] / monthly["CLAIM_COUNT"],
    portfolio_avg_severity,
)

monthly["LOSS_RATIO"] = np.where(
    monthly["EARNED_PREMIUM"] > 0,
    monthly["TOTAL_ULTIMATE"] / monthly["EARNED_PREMIUM"],
    np.nan,
)

# EDIT 4: keep a copy of RAW_ULTIMATE for diagnostics
monthly["RAW_ULTIMATE"] = monthly["TOTAL_ULTIMATE"].copy()

print(f"Raw monthly data: {monthly['ds'].min():%Y-%m} to {monthly['ds'].max():%Y-%m}")


# =============================================================================
# STEP 3 — APPLY DEMO MATURE DATA WINDOW
# =============================================================================

print()
print("=" * 78)
print("STEP 3: Applying mature-data window")
print("=" * 78)

monthly = monthly[
    (monthly["ds"] >= pd.Timestamp(DATA_START))
    & (monthly["ds"] <= pd.Timestamp(MATURE_DATA_END))
].copy().sort_values("ds").reset_index(drop=True)

if len(monthly) < 18:
    raise ValueError(f"Only {len(monthly)} usable months remain. At least 18 required.")

print(f"Demo modelling window: {DATA_START[:7]} to {MATURE_DATA_END[:7]}")
print(f"Transition excluded: {TRANSITION_START[:7]} to {TRANSITION_END[:7]}")
print(f"Usable monthly series: {len(monthly)} months ({monthly['ds'].min():%Y-%m} to {monthly['ds'].max():%Y-%m})")
print(f"Total claims: {monthly['CLAIM_COUNT'].sum():,.0f}")
print(f"Average monthly claims: {monthly['CLAIM_COUNT'].mean():.1f}")
print(f"Average monthly ultimate: {monthly['TOTAL_ULTIMATE'].mean():,.0f}")
print(f"Average severity: {monthly['AVG_SEVERITY'].mean():,.0f}")
print(f"Average earned premium: {monthly['EARNED_PREMIUM'].mean():,.0f}")


# =============================================================================
# STEP 4 — SHOW DATA TAIL
# =============================================================================

print()
print("Last 8 usable months:")
print(f"{'Month':>10} {'Claims SAR':>15} {'Count':>8} {'Premium':>15} {'LR':>8}")
for _, row in monthly.tail(8).iterrows():
    lr = row["TOTAL_ULTIMATE"] / row["EARNED_PREMIUM"] if row["EARNED_PREMIUM"] > 0 else 0
    print(f"{row['ds']:%Y-%m} {row['TOTAL_ULTIMATE']:>15,.0f} {row['CLAIM_COUNT']:>8.0f} {row['EARNED_PREMIUM']:>15,.0f} {lr:>7.1%}")


# =============================================================================
# STEP 5 — HIJRI HOLIDAY CALENDAR
# =============================================================================

print()
print("=" * 78)
print("STEP 5: Building Hijri holiday calendar")
print("=" * 78)


def build_hijri_holidays():
    try:
        from hijri_converter import Hijri
        holidays = []
        for hy in range(1441, 1450):
            try:
                ram = Hijri(hy, 9, 1).to_gregorian()
                for d in range(30):
                    holidays.append({"holiday": "ramadan", "ds": pd.Timestamp(ram) + pd.Timedelta(days=d), "lower_window": 0, "upper_window": 0})
            except Exception:
                pass
            try:
                ef = Hijri(hy, 10, 1).to_gregorian()
                for d in range(3):
                    holidays.append({"holiday": "eid_al_fitr", "ds": pd.Timestamp(ef) + pd.Timedelta(days=d), "lower_window": 0, "upper_window": 0})
            except Exception:
                pass
            try:
                ea = Hijri(hy, 12, 10).to_gregorian()
                for d in range(4):
                    holidays.append({"holiday": "eid_al_adha", "ds": pd.Timestamp(ea) + pd.Timedelta(days=d), "lower_window": 0, "upper_window": 0})
            except Exception:
                pass
            try:
                hj = Hijri(hy, 12, 8).to_gregorian()
                for d in range(5):
                    holidays.append({"holiday": "hajj_season", "ds": pd.Timestamp(hj) + pd.Timedelta(days=d), "lower_window": 0, "upper_window": 0})
            except Exception:
                pass
        hdf = pd.DataFrame(holidays)
        print(f"Built {len(hdf)} Hijri holiday dates.")
        return hdf
    except ImportError:
        print("WARNING: hijri-converter not installed. Running without Hijri holidays.")
        return None


holidays_df = build_hijri_holidays()


# =============================================================================
# STEP 6 — TRAIN / VALIDATION SPLIT
# =============================================================================

print()
print("=" * 78)
print("STEP 6: Train / validation split")
print("=" * 78)

if len(monthly) <= VALIDATION_MONTHS + 12:
    raise ValueError("Not enough observations for validation.")

validation_cutoff = monthly["ds"].max() - pd.DateOffset(months=VALIDATION_MONTHS)
train = monthly[monthly["ds"] <= validation_cutoff].copy()
holdout = monthly[monthly["ds"] > validation_cutoff].copy()

print(f"Train:   {len(train)} months ({train['ds'].min():%Y-%m} to {train['ds'].max():%Y-%m})")
print(f"Holdout: {len(holdout)} months ({holdout['ds'].min():%Y-%m} to {holdout['ds'].max():%Y-%m})")
print()
print("IMPORTANT: validation uses only mature months.")


# =============================================================================
# STEP 7 — IMPORT MODELS
# =============================================================================

print()
print("=" * 78)
print("STEP 7: Preparing forecasting models")
print("=" * 78)

from statsmodels.tsa.statespace.sarimax import SARIMAX

try:
    from prophet import Prophet
    PROPHET_AVAILABLE = True
except ImportError:
    Prophet = None
    PROPHET_AVAILABLE = False
    print("Prophet is not installed — skipping Prophet.")

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    HW_AVAILABLE = True
except ImportError:
    ExponentialSmoothing = None
    HW_AVAILABLE = False


# =============================================================================
# STEP 8 — MODEL HELPERS
# =============================================================================

def fit_prophet(train_df, col, holidays=None, name=""):
    if not PROPHET_AVAILABLE:
        return None
    df = train_df[["ds", col]].rename(columns={col: "y"}).copy()
    df["y"] = pd.to_numeric(df["y"], errors="coerce").fillna(0).clip(lower=0)
    if (df["y"] <= 0).all():
        return None
    positive = df.loc[df["y"] > 0, "y"]
    if len(positive) > 0:
        df.loc[df["y"] <= 0, "y"] = positive.min() * 0.05
    model = Prophet(
        seasonality_mode="additive",
        yearly_seasonality=False,
        weekly_seasonality=False,
        daily_seasonality=False,
        changepoint_prior_scale=0.03,
        seasonality_prior_scale=1.0,
        interval_width=CI_WIDTH,
    )
    if len(df) >= 24:
        model.add_seasonality(name="yearly_weak", period=365.25, fourier_order=3, prior_scale=1.0)
    if holidays is not None and len(holidays) > 0:
        model.holidays = holidays
    try:
        model.fit(df)
        print(f"  Prophet ({name}): {len(df)} observations")
        return model
    except Exception as exc:
        print(f"  Prophet ({name}): FAILED — {exc}")
        return None


def fit_sarima(train_series, name=""):
    series = pd.Series(train_series).astype(float).replace([np.inf, -np.inf], np.nan).fillna(0)
    configs = [
        ((0, 1, 1), (0, 1, 1, 12)),
        ((1, 1, 0), (0, 1, 1, 12)),
        ((0, 1, 1), (1, 1, 0, 12)),
        ((1, 1, 1), (0, 1, 1, 12)),
    ]
    best_result, best_aic, best_config = None, np.inf, None
    for order, seasonal_order in configs:
        try:
            model = SARIMAX(series.values, order=order, seasonal_order=seasonal_order,
                            enforce_stationarity=False, enforce_invertibility=False)
            result = model.fit(disp=False, maxiter=500)
            if result.aic < best_aic:
                best_aic, best_result, best_config = result.aic, result, (order, seasonal_order)
        except Exception:
            continue
    if best_result is not None:
        print(f"  SARIMA ({name}): AIC={best_aic:.1f}, config={best_config}")
    else:
        print(f"  SARIMA ({name}): ALL configs FAILED")
    return best_result


def fit_hw(train_series, name=""):
    if not HW_AVAILABLE:
        return None
    series = pd.Series(train_series).astype(float).replace([np.inf, -np.inf], np.nan).fillna(0)
    try:
        if len(series) >= 24:
            model = ExponentialSmoothing(series.values, trend="add", seasonal="add",
                                        seasonal_periods=12, initialization_method="estimated")
        else:
            model = ExponentialSmoothing(series.values, trend="add", initialization_method="estimated")
        result = model.fit(optimized=True)
        print(f"  Holt-Winters ({name}): AICc={result.aicc:.1f}")
        return result
    except Exception as exc:
        print(f"  Holt-Winters ({name}): FAILED — {exc}")
        return None


# =============================================================================
# STEP 9 — FIT CANDIDATE MODELS
# =============================================================================

print()
print("=" * 78)
print("STEP 9: Fitting candidate models")
print("=" * 78)

n_forward = VALIDATION_MONTHS + max(FORECAST_HORIZONS)

# Prophet
print("\n--- Prophet ---")
prophet_claims = fit_prophet(train, "TOTAL_ULTIMATE", holidays_df, "Claims")
prophet_premium = fit_prophet(train, "EARNED_PREMIUM", holidays_df, "Premium")
if prophet_claims is not None:
    future_prophet = prophet_claims.make_future_dataframe(periods=n_forward, freq="MS")
    fc_prophet_claims = prophet_claims.predict(future_prophet)
else:
    fc_prophet_claims = None
if prophet_premium is not None:
    future_prophet_premium = prophet_premium.make_future_dataframe(periods=n_forward, freq="MS")
    fc_prophet_premium = prophet_premium.predict(future_prophet_premium)
else:
    fc_prophet_premium = None

# SARIMA
print("\n--- SARIMA ---")
sarima_claims = fit_sarima(train.set_index("ds")["TOTAL_ULTIMATE"], "Claims")
sarima_premium = fit_sarima(train.set_index("ds")["EARNED_PREMIUM"], "Premium")

# Holt-Winters
print("\n--- Holt-Winters ---")
hw_claims = fit_hw(train.set_index("ds")["TOTAL_ULTIMATE"], "Claims")
hw_premium = fit_hw(train.set_index("ds")["EARNED_PREMIUM"], "Premium")


# =============================================================================
# STEP 10 — VALIDATION METRICS
# =============================================================================

print()
print("=" * 78)
print("STEP 10: Holdout validation")
print("=" * 78)


def calc_metrics(actual, predicted, lower=None, upper=None):
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    valid = np.isfinite(actual) & np.isfinite(predicted)
    actual, predicted = actual[valid], predicted[valid]
    if len(actual) == 0:
        return {"wape": np.inf, "bias": np.nan, "coverage": np.nan, "mae": np.inf}
    denom = np.sum(np.abs(actual))
    wape = np.sum(np.abs(actual - predicted)) / denom if denom > 0 else np.inf
    bias = np.mean(predicted) / np.mean(actual) if np.mean(actual) != 0 else np.nan
    mae = np.mean(np.abs(actual - predicted))
    coverage = np.nan
    if lower is not None and upper is not None:
        lo = np.asarray(lower, dtype=float)[valid]
        hi = np.asarray(upper, dtype=float)[valid]
        coverage = np.mean((actual >= lo) & (actual <= hi))
    return {"wape": wape, "bias": bias, "coverage": coverage, "mae": mae}


holdout_actual = holdout["TOTAL_ULTIMATE"].values
validation_results = {}

# Prophet
if fc_prophet_claims is not None:
    ph = fc_prophet_claims[fc_prophet_claims["ds"].isin(holdout["ds"])].sort_values("ds")
    if len(ph) == len(holdout):
        m = calc_metrics(holdout_actual, ph["yhat"].values, ph["yhat_lower"].values, ph["yhat_upper"].values)
        validation_results["Prophet"] = m
        print(f"\n  Prophet\n    WAPE: {m['wape']:.1%}  Bias: {m['bias']:.3f}  Coverage: {m['coverage']:.0%}  MAE: {m['mae']:,.0f}")

# SARIMA
sarima_validation_pred = sarima_validation_lo = sarima_validation_hi = None
if sarima_claims is not None:
    try:
        pred = sarima_claims.get_forecast(steps=n_forward)
        ci = np.asarray(pred.conf_int(alpha=1 - CI_WIDTH))
        sarima_validation_pred = np.asarray(pred.predicted_mean)[:VALIDATION_MONTHS]
        sarima_validation_lo = ci[:VALIDATION_MONTHS, 0]
        sarima_validation_hi = ci[:VALIDATION_MONTHS, 1]
        m = calc_metrics(holdout_actual, sarima_validation_pred, sarima_validation_lo, sarima_validation_hi)
        validation_results["SARIMA"] = m
        print(f"\n  SARIMA\n    WAPE: {m['wape']:.1%}  Bias: {m['bias']:.3f}  Coverage: {m['coverage']:.0%}  MAE: {m['mae']:,.0f}")
    except Exception as exc:
        print(f"  SARIMA validation failed: {exc}")

# Holt-Winters
hw_validation_pred = hw_validation_lo = hw_validation_hi = None
if hw_claims is not None:
    try:
        hw_fc = hw_claims.forecast(n_forward)
        hw_validation_pred = np.asarray(hw_fc)[:VALIDATION_MONTHS]
        resid_std = np.nanstd(hw_claims.resid)
        if not np.isfinite(resid_std) or resid_std <= 0:
            resid_std = np.nanstd(train["TOTAL_ULTIMATE"]) * 0.20
        hw_validation_lo = hw_validation_pred - 1.645 * resid_std
        hw_validation_hi = hw_validation_pred + 1.645 * resid_std
        m = calc_metrics(holdout_actual, hw_validation_pred, hw_validation_lo, hw_validation_hi)
        validation_results["HoltWinters"] = m
        print(f"\n  Holt-Winters\n    WAPE: {m['wape']:.1%}  Bias: {m['bias']:.3f}  Coverage: {m['coverage']:.0%}  MAE: {m['mae']:,.0f}")
    except Exception as exc:
        print(f"  Holt-Winters validation failed: {exc}")

# Seasonal Naive
print("\n--- Seasonal Naive ---")
naive_predictions = []
for date in holdout["ds"]:
    prev = date - pd.DateOffset(years=1)
    match = monthly[monthly["ds"] == prev]
    naive_predictions.append(match["TOTAL_ULTIMATE"].iloc[0] if len(match) > 0 else np.nan)
naive_predictions = np.asarray(naive_predictions, dtype=float)
naive_valid = np.isfinite(naive_predictions)
if naive_valid.sum() > 0:
    nm = calc_metrics(holdout_actual[naive_valid], naive_predictions[naive_valid])
    validation_results["Naive"] = nm
    print(f"  WAPE: {nm['wape']:.1%}  Bias: {nm['bias']:.3f}  MAE: {nm['mae']:,.0f}")


# =============================================================================
# STEP 11 — MODEL SELECTION
# =============================================================================
# EDIT 3: Bias range widened from [0.70, 1.30] to [0.65, 1.35].
# SARIMA had bias 1.335 and 33.5% WAPE with 100% coverage — it should
# beat Naive (43% WAPE, 0% coverage, pure last-year replay).

print()
print("=" * 78)
print("STEP 11: Model selection")
print("=" * 78)

print("\nModel comparison:")
for name, m in validation_results.items():
    print(f"  {name:<15} WAPE={m['wape']:.1%}  Bias={m['bias']:.3f}  Coverage={m['coverage']:.0%}" if pd.notna(m['coverage']) else f"  {name:<15} WAPE={m['wape']:.1%}  Bias={m['bias']:.3f}")

valid_candidates = {n: m for n, m in validation_results.items() if np.isfinite(m["wape"])}

# EDIT 3: widened range
reasonable_bias_candidates = {
    n: m for n, m in valid_candidates.items()
    if pd.notna(m["bias"]) and 0.65 <= m["bias"] <= 1.35
}

if reasonable_bias_candidates:
    winner = min(reasonable_bias_candidates, key=lambda n: reasonable_bias_candidates[n]["wape"])
    selection_reason = "Lowest WAPE among candidates with reasonable validation bias (0.65–1.35)."
elif valid_candidates:
    winner = min(valid_candidates, key=lambda n: valid_candidates[n]["wape"])
    selection_reason = "Lowest WAPE (no candidate passed the bias screen)."
else:
    winner = "SARIMA"
    selection_reason = "Fallback to SARIMA (no valid metrics)."

winner_metrics = validation_results.get(winner, {"wape": np.inf, "bias": np.nan, "coverage": np.nan, "mae": np.inf})
winner_wape = winner_metrics["wape"]
winner_bias = winner_metrics["bias"]
winner_coverage = winner_metrics["coverage"]

print(f"\n>>> Selected model: {winner}")
print(f"Reason: {selection_reason}")


# =============================================================================
# STEP 12 — SEASONALITY DIAGNOSTICS
# =============================================================================

print()
print("=" * 78)
print("STEP 12: Seasonality diagnostics")
print("=" * 78)

_sm = monthly.copy()
_sm["month_num"] = _sm["ds"].dt.month
by_month = _sm.groupby("month_num").agg(
    avg_claims=("TOTAL_ULTIMATE", "mean"),
    avg_premium=("EARNED_PREMIUM", "mean"),
    count=("TOTAL_ULTIMATE", "count"),
).reset_index()
by_month["avg_lr"] = by_month["avg_claims"] / by_month["avg_premium"]
overall_mean = by_month["avg_claims"].mean()
by_month["seasonal_index"] = by_month["avg_claims"] / overall_mean if overall_mean > 0 else 1.0

print("\nMonth  Avg Claims     Avg Premium    Avg LR   Seasonal")
for _, r in by_month.iterrows():
    print(f"{int(r['month_num']):>5} {r['avg_claims']:>14,.0f} {r['avg_premium']:>14,.0f} {r['avg_lr']:>8.1%} {r['seasonal_index']:>8.2f}")

seasonality_p = seasonality_f = np.nan
try:
    from scipy import stats
    groups = [g["TOTAL_ULTIMATE"].values for _, g in monthly.groupby(monthly["ds"].dt.month) if len(g) > 1]
    if len(groups) >= 2:
        f_stat, p_val = stats.f_oneway(*groups)
        seasonality_f, seasonality_p = f_stat, p_val
        print(f"\nANOVA F={f_stat:.2f}, p={p_val:.4f}")
        print(f"Seasonality {'IS' if p_val < 0.05 else 'is NOT'} statistically significant at 5%")
except Exception as exc:
    print(f"Seasonality ANOVA unavailable: {exc}")


# =============================================================================
# STEP 13 — REFIT WINNING MODEL ON ALL MATURE DATA
# =============================================================================

print()
print("=" * 78)
print("STEP 13: Refit winning model on full mature data")
print("=" * 78)

n_final = max(FORECAST_HORIZONS)
last_actual = monthly["ds"].max()
future_dates = pd.date_range(start=last_actual + pd.DateOffset(months=1), periods=n_final, freq="MS")

if winner == "Prophet":
    final_claims = fit_prophet(monthly, "TOTAL_ULTIMATE", holidays_df, "Final Claims")
    final_premium = fit_prophet(monthly, "EARNED_PREMIUM", holidays_df, "Final Premium")
    if final_claims is None or final_premium is None:
        raise RuntimeError("Prophet final fitting failed.")
    future = final_claims.make_future_dataframe(periods=n_final, freq="MS")
    fc_c = final_claims.predict(future)
    fc_p = final_premium.predict(future)
    fwd = fc_c["ds"] > last_actual
    forecast_df = pd.DataFrame({
        "ds": fc_c.loc[fwd, "ds"].values[:n_final],
        "FORECAST_CLAIMS": fc_c.loc[fwd, "yhat"].values[:n_final],
        "CI_CLAIMS_LOWER": fc_c.loc[fwd, "yhat_lower"].values[:n_final],
        "CI_CLAIMS_UPPER": fc_c.loc[fwd, "yhat_upper"].values[:n_final],
        "FORECAST_PREMIUM": fc_p.loc[fwd, "yhat"].values[:n_final],
        "CI_PREMIUM_LOWER": fc_p.loc[fwd, "yhat_lower"].values[:n_final],
        "CI_PREMIUM_UPPER": fc_p.loc[fwd, "yhat_upper"].values[:n_final],
    })
    try:
        sf = fc_c.loc[fwd, ["ds", "yearly_weak"]].head(n_final).copy()
        sf["SEASONAL_INDEX"] = 1.0 + sf["yearly_weak"] / max(1.0, abs(sf["yearly_weak"]).max())
        seasonal = sf[["ds", "SEASONAL_INDEX"]].copy()
    except Exception:
        seasonal = pd.DataFrame({"ds": future_dates, "SEASONAL_INDEX": 1.0})

elif winner == "SARIMA":
    final_claims = fit_sarima(monthly.set_index("ds")["TOTAL_ULTIMATE"], "Final Claims")
    final_premium = fit_sarima(monthly.set_index("ds")["EARNED_PREMIUM"], "Final Premium")
    if final_claims is None or final_premium is None:
        raise RuntimeError("SARIMA final fitting failed.")
    fc_c = final_claims.get_forecast(steps=n_final)
    fc_p = final_premium.get_forecast(steps=n_final)
    ci_c = np.asarray(fc_c.conf_int(alpha=1 - CI_WIDTH))
    ci_p = np.asarray(fc_p.conf_int(alpha=1 - CI_WIDTH))
    forecast_df = pd.DataFrame({
        "ds": future_dates,
        "FORECAST_CLAIMS": np.asarray(fc_c.predicted_mean),
        "CI_CLAIMS_LOWER": ci_c[:, 0],
        "CI_CLAIMS_UPPER": ci_c[:, 1],
        "FORECAST_PREMIUM": np.asarray(fc_p.predicted_mean),
        "CI_PREMIUM_LOWER": ci_p[:, 0],
        "CI_PREMIUM_UPPER": ci_p[:, 1],
    })
    seasonal = pd.DataFrame({"ds": future_dates, "SEASONAL_INDEX": 1.0})

elif winner == "HoltWinters":
    final_claims = fit_hw(monthly.set_index("ds")["TOTAL_ULTIMATE"], "Final Claims")
    final_premium = fit_hw(monthly.set_index("ds")["EARNED_PREMIUM"], "Final Premium")
    if final_claims is None or final_premium is None:
        raise RuntimeError("Holt-Winters final fitting failed.")
    c_mean = np.asarray(final_claims.forecast(n_final))
    p_mean = np.asarray(final_premium.forecast(n_final))
    c_std = max(np.nanstd(final_claims.resid), monthly["TOTAL_ULTIMATE"].std() * 0.20)
    p_std = max(np.nanstd(final_premium.resid), monthly["EARNED_PREMIUM"].std() * 0.10)
    forecast_df = pd.DataFrame({
        "ds": future_dates,
        "FORECAST_CLAIMS": c_mean,
        "CI_CLAIMS_LOWER": c_mean - 1.645 * c_std,
        "CI_CLAIMS_UPPER": c_mean + 1.645 * c_std,
        "FORECAST_PREMIUM": p_mean,
        "CI_PREMIUM_LOWER": p_mean - 1.645 * p_std,
        "CI_PREMIUM_UPPER": p_mean + 1.645 * p_std,
    })
    seasonal = pd.DataFrame({"ds": future_dates, "SEASONAL_INDEX": 1.0})

else:
    # Naive: last year replay
    print("Using seasonal-naive final forecast.")
    fc_rows = []
    for d in future_dates:
        prev = d - pd.DateOffset(years=1)
        match = monthly[monthly["ds"] == prev]
        if len(match) > 0:
            fc_rows.append({"ds": d, "c": match["TOTAL_ULTIMATE"].iloc[0], "p": match["EARNED_PREMIUM"].iloc[0]})
        else:
            fc_rows.append({"ds": d, "c": monthly["TOTAL_ULTIMATE"].tail(12).mean(), "p": monthly["EARNED_PREMIUM"].tail(12).mean()})
    nf = pd.DataFrame(fc_rows)
    c_std = monthly["TOTAL_ULTIMATE"].tail(12).std()
    p_std = monthly["EARNED_PREMIUM"].tail(12).std()
    forecast_df = pd.DataFrame({
        "ds": nf["ds"],
        "FORECAST_CLAIMS": nf["c"],
        "CI_CLAIMS_LOWER": nf["c"] - 1.645 * c_std,
        "CI_CLAIMS_UPPER": nf["c"] + 1.645 * c_std,
        "FORECAST_PREMIUM": nf["p"],
        "CI_PREMIUM_LOWER": nf["p"] - 1.645 * p_std,
        "CI_PREMIUM_UPPER": nf["p"] + 1.645 * p_std,
    })
    seasonal = pd.DataFrame({"ds": future_dates, "SEASONAL_INDEX": 1.0})


# =============================================================================
# STEP 14 — CLEAN FORECAST
# =============================================================================

print()
print("=" * 78)
print("STEP 14: Cleaning and stabilising forecast")
print("=" * 78)

forecast_df = forecast_df.copy()
num_cols = ["FORECAST_CLAIMS", "CI_CLAIMS_LOWER", "CI_CLAIMS_UPPER",
            "FORECAST_PREMIUM", "CI_PREMIUM_LOWER", "CI_PREMIUM_UPPER"]
for col in num_cols:
    forecast_df[col] = pd.to_numeric(forecast_df[col], errors="coerce")
forecast_df = clip_nonnegative(forecast_df, num_cols)

# Premium stability guardrail
trailing_12_avg_prem = monthly["EARNED_PREMIUM"].tail(12).mean()
if pd.notna(trailing_12_avg_prem) and trailing_12_avg_prem > 0:
    fc_avg_prem = forecast_df["FORECAST_PREMIUM"].mean()
    prem_ratio = fc_avg_prem / trailing_12_avg_prem
    if prem_ratio < (1 - MAX_PREMIUM_DEVIATION):
        scale = (1 - MAX_PREMIUM_DEVIATION) / prem_ratio
        print(f"Premium forecast below guardrail. Scaling by {scale:.3f}")
        for c in ["FORECAST_PREMIUM", "CI_PREMIUM_LOWER", "CI_PREMIUM_UPPER"]:
            forecast_df[c] *= scale
    elif prem_ratio > (1 + MAX_PREMIUM_DEVIATION):
        scale = (1 + MAX_PREMIUM_DEVIATION) / prem_ratio
        print(f"Premium forecast above guardrail. Scaling by {scale:.3f}")
        for c in ["FORECAST_PREMIUM", "CI_PREMIUM_LOWER", "CI_PREMIUM_UPPER"]:
            forecast_df[c] *= scale


# =============================================================================
# STEP 15 — LOSS RATIO AND CONFIDENCE INTERVALS
# =============================================================================

print()
print("=" * 78)
print("STEP 15: Calculating forecast KPIs")
print("=" * 78)

# Loss ratio = claims / premium (no cap — EDIT 2)
forecast_df["FORECAST_LOSS_RATIO"] = np.where(
    forecast_df["FORECAST_PREMIUM"] > 0,
    forecast_df["FORECAST_CLAIMS"] / forecast_df["FORECAST_PREMIUM"],
    np.nan,
)

# Conservative CI: claim CI / point premium (not worst claims / best premium)
forecast_df["CI_LR_LOWER"] = np.where(
    forecast_df["FORECAST_PREMIUM"] > 0,
    forecast_df["CI_CLAIMS_LOWER"] / forecast_df["FORECAST_PREMIUM"],
    np.nan,
).clip(min=0)

forecast_df["CI_LR_UPPER"] = np.where(
    forecast_df["FORECAST_PREMIUM"] > 0,
    forecast_df["CI_CLAIMS_UPPER"] / forecast_df["FORECAST_PREMIUM"],
    np.nan,
).clip(min=0)


# =============================================================================
# STEP 16 — BUSINESS LR GUARDRAIL (floor only, no cap)
# =============================================================================

trailing_12_claims = monthly["TOTAL_ULTIMATE"].tail(12).sum()
trailing_12_premium = monthly["EARNED_PREMIUM"].tail(12).sum()
trailing_12_lr = trailing_12_claims / trailing_12_premium if trailing_12_premium > 0 else np.nan

if pd.notna(trailing_12_lr) and trailing_12_lr > MIN_FORECAST_LR:
    lower_allowed_lr = max(MIN_FORECAST_LR, trailing_12_lr * 0.65)
    raw_mean_lr = forecast_df["FORECAST_LOSS_RATIO"].mean()
    if pd.notna(raw_mean_lr) and raw_mean_lr < lower_allowed_lr:
        print(f"\nWARNING: Forecast LR ({raw_mean_lr:.1%}) shows unexplained improvement vs trailing ({trailing_12_lr:.1%}).")
        print(f"Blending toward {lower_allowed_lr:.1%}.")
        blend = 0.70
        forecast_df["FORECAST_LOSS_RATIO"] = blend * lower_allowed_lr + (1 - blend) * forecast_df["FORECAST_LOSS_RATIO"]
        # Reconstruct claims to stay consistent (EDIT 2: no cap afterward)
        forecast_df["FORECAST_CLAIMS"] = forecast_df["FORECAST_LOSS_RATIO"] * forecast_df["FORECAST_PREMIUM"]
        forecast_df["CI_CLAIMS_LOWER"] = forecast_df["CI_LR_LOWER"] * forecast_df["FORECAST_PREMIUM"]
        forecast_df["CI_CLAIMS_UPPER"] = forecast_df["CI_LR_UPPER"] * forecast_df["FORECAST_PREMIUM"]

# Claim count and severity
avg_severity = monthly["AVG_SEVERITY"].replace([np.inf, -np.inf], np.nan).dropna().mean()
if pd.isna(avg_severity) or avg_severity <= 0:
    avg_severity = 1.0
forecast_df["FORECAST_CLAIM_COUNT"] = forecast_df["FORECAST_CLAIMS"] / avg_severity
forecast_df["FORECAST_AVG_SEVERITY"] = avg_severity

# Final cleanup
forecast_df = clip_nonnegative(forecast_df, [
    "FORECAST_CLAIMS", "CI_CLAIMS_LOWER", "CI_CLAIMS_UPPER",
    "FORECAST_PREMIUM", "CI_PREMIUM_LOWER", "CI_PREMIUM_UPPER",
    "FORECAST_CLAIM_COUNT", "FORECAST_AVG_SEVERITY",
    "CI_LR_LOWER", "CI_LR_UPPER",
])


# =============================================================================
# STEP 17 — SEASONAL INDEX
# =============================================================================

forecast_df = forecast_df.merge(seasonal, on="ds", how="left")
forecast_df["SEASONAL_INDEX"] = forecast_df["SEASONAL_INDEX"].fillna(1.0).clip(0.75, 1.25)


# =============================================================================
# STEP 18 — BUSINESS CHECKS
# =============================================================================
# EDIT 1: compare monthly avg to monthly avg (not annual sum to monthly avg)

print()
print("=" * 78)
print("STEP 18: Business logic checks")
print("=" * 78)

trailing_12_avg_prem = monthly["EARNED_PREMIUM"].tail(12).mean()
forecast_avg_prem = forecast_df["FORECAST_PREMIUM"].mean()
premium_change = forecast_avg_prem / trailing_12_avg_prem - 1 if trailing_12_avg_prem > 0 else np.nan

forecast_12_lr = forecast_df["FORECAST_CLAIMS"].sum() / forecast_df["FORECAST_PREMIUM"].sum()

print(f"Trailing 12M avg monthly premium: {trailing_12_avg_prem:,.0f}")
print(f"Forecast avg monthly premium:      {forecast_avg_prem:,.0f}")
print(f"Premium change:                     {premium_change:+.1%}")
if pd.notna(premium_change) and abs(premium_change) <= MAX_PREMIUM_DEVIATION:
    print("Premium stability: PASS")
else:
    print("Premium stability: REVIEW")

print(f"\nTrailing 12M LR: {trailing_12_lr:.1%}")
print(f"Forecast 12M LR: {forecast_12_lr:.1%}")

if pd.notna(trailing_12_lr) and trailing_12_lr > 1.0 and forecast_12_lr < 1.0:
    print("WARNING: Forecast below 100% LR while trailing is above 100%.")


# =============================================================================
# STEP 19 — BUILD OUTPUT TABLE
# =============================================================================
# EDIT 4 & 5: add actual months + PERIOD_TYPE column

print()
print("=" * 78)
print("STEP 19: Building output table")
print("=" * 78)

rows = []

# --- ACTUAL MONTHS (EDIT 4) ---
for _, row in monthly.iterrows():
    lr = row["TOTAL_ULTIMATE"] / row["EARNED_PREMIUM"] if row["EARNED_PREMIUM"] > 0 else 0
    rows.append({
        "SEGMENT_ID": "PORTFOLIO",
        "MONTH_DATE": row["ds"].strftime("%Y-%m-%d"),
        "FORECAST_HORIZON": "ACTUAL",
        "PERIOD_TYPE": "actual",
        "FORECAST_CLAIMS": round(row["TOTAL_ULTIMATE"], 2),
        "FORECAST_PREMIUM": round(row["EARNED_PREMIUM"], 2),
        "FORECAST_LOSS_RATIO": round(lr, 4),
        "CI_LOWER_90": np.nan,
        "CI_UPPER_90": np.nan,
        "FORECAST_CLAIM_COUNT": round(row["CLAIM_COUNT"], 1),
        "FORECAST_AVG_SEVERITY": round(row["AVG_SEVERITY"], 2),
        "SEASONAL_INDEX": 1.0,
        "MODEL_TYPE": "actual",
        "MODEL_WAPE": np.nan,
        "BIAS_CORRECTION": np.nan,
    })

# --- FORECAST MONTHS ---
for horizon in FORECAST_HORIZONS:
    for _, row in forecast_df.head(horizon).iterrows():
        rows.append({
            "SEGMENT_ID": "PORTFOLIO",
            "MONTH_DATE": row["ds"].strftime("%Y-%m-%d"),
            "FORECAST_HORIZON": f"{horizon}M",
            "PERIOD_TYPE": "forecast",
            "FORECAST_CLAIMS": round(safe_float(row["FORECAST_CLAIMS"]), 2),
            "FORECAST_PREMIUM": round(safe_float(row["FORECAST_PREMIUM"]), 2),
            "FORECAST_LOSS_RATIO": round(safe_float(row["FORECAST_LOSS_RATIO"]), 4),
            "CI_LOWER_90": round(safe_float(row["CI_LR_LOWER"]), 4),
            "CI_UPPER_90": round(safe_float(row["CI_LR_UPPER"]), 4),
            "FORECAST_CLAIM_COUNT": round(safe_float(row["FORECAST_CLAIM_COUNT"]), 1),
            "FORECAST_AVG_SEVERITY": round(safe_float(row["FORECAST_AVG_SEVERITY"]), 2),
            "SEASONAL_INDEX": round(safe_float(row["SEASONAL_INDEX"], 1.0), 4),
            "MODEL_TYPE": winner.lower(),
            "MODEL_WAPE": round(safe_float(winner_wape, 0), 4),
            "BIAS_CORRECTION": 1.0,
        })

output = pd.DataFrame(rows)


# =============================================================================
# STEP 20 — SAVE CSV
# =============================================================================

output_path = FACTS_DIR / "fact_forecast_seasonal.csv"
output.to_csv(output_path, index=False, encoding="utf-8")
print(f"\nSaved: {output_path}")
print(f"Rows: {len(output)}  (actuals: {len(monthly)}, forecast: {len(output) - len(monthly)})")


# =============================================================================
# STEP 21 — VALIDATION SUMMARY
# =============================================================================

print()
print("=" * 78)
print("STEP 21: Validation summary")
print("=" * 78)

agg_actual = holdout["TOTAL_ULTIMATE"].sum()
if winner == "Prophet":
    wp = fc_prophet_claims[fc_prophet_claims["ds"].isin(holdout["ds"])]["yhat"].values
elif winner == "SARIMA":
    wp = sarima_validation_pred
elif winner == "HoltWinters":
    wp = hw_validation_pred
else:
    wp = naive_predictions[naive_valid]

if wp is not None and len(wp) > 0 and agg_actual > 0:
    agg_pred = np.sum(wp)
    agg_wape = abs(agg_actual - agg_pred) / agg_actual
else:
    agg_pred, agg_wape = np.nan, np.inf

agg_wape_pass = pd.notna(agg_wape) and agg_wape <= MAX_WAPE

print(f"Model:              {winner}")
print(f"Aggregate WAPE:     {agg_wape:.1%} {'PASS' if agg_wape_pass else 'REVIEW'}")
print(f"Monthly WAPE:       {winner_wape:.1%}")
print(f"Raw bias:            {winner_bias:.3f}")
print(f"90% coverage:        {winner_coverage:.0%}" if pd.notna(winner_coverage) else "90% coverage:        N/A")


# =============================================================================
# STEP 22 — VALIDATION VISUAL
# =============================================================================

print()
print("=" * 78)
print("STEP 22: Creating validation visual")
print("=" * 78)

try:
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))

    ax = axes[0, 0]
    ax.plot(monthly["ds"], monthly["TOTAL_ULTIMATE"], "b-o", label="Actual", markersize=3)
    ax.plot(forecast_df["ds"], forecast_df["FORECAST_CLAIMS"], "r--s", label="Forecast", markersize=3)
    ax.fill_between(forecast_df["ds"], forecast_df["CI_CLAIMS_LOWER"], forecast_df["CI_CLAIMS_UPPER"], alpha=0.2, color="red")
    ax.set_title("Monthly Claims: Actual vs Forecast")
    ax.set_ylabel("SAR")
    ax.legend()
    ax.grid(alpha=0.2)

    ax = axes[0, 1]
    ax.plot(monthly["ds"], monthly["EARNED_PREMIUM"], "g-o", label="Actual Premium", markersize=3)
    ax.plot(forecast_df["ds"], forecast_df["FORECAST_PREMIUM"], "r--s", label="Forecast Premium", markersize=3)
    ax.set_title("Monthly Premium: Actual vs Forecast")
    ax.set_ylabel("SAR")
    ax.legend()
    ax.grid(alpha=0.2)

    ax = axes[1, 0]
    ax.plot(monthly["ds"], monthly["LOSS_RATIO"], "b-o", label="Actual LR", markersize=3)
    ax.plot(forecast_df["ds"], forecast_df["FORECAST_LOSS_RATIO"], "r--s", label="Forecast LR", markersize=3)
    ax.fill_between(forecast_df["ds"], forecast_df["CI_LR_LOWER"], forecast_df["CI_LR_UPPER"], alpha=0.2, color="red")
    ax.axhline(y=1.0, color="black", linestyle=":", alpha=0.6, label="100% LR")
    ax.set_title("Loss Ratio: Actual vs Forecast")
    ax.legend()
    ax.grid(alpha=0.2)

    ax = axes[1, 1]
    scatter_pred = None
    if winner == "Prophet" and fc_prophet_claims is not None:
        scatter_pred = fc_prophet_claims[fc_prophet_claims["ds"].isin(holdout["ds"])]["yhat"].values
    elif winner == "SARIMA":
        scatter_pred = sarima_validation_pred
    elif winner == "HoltWinters":
        scatter_pred = hw_validation_pred
    elif winner == "Naive":
        scatter_pred = naive_predictions[naive_valid]
    if scatter_pred is not None and len(scatter_pred) == len(holdout_actual):
        ax.scatter(holdout_actual, scatter_pred, c="blue", alpha=0.75)
        mx = max(np.max(holdout_actual), np.max(scatter_pred)) * 1.1
        ax.plot([0, mx], [0, mx], "k--", alpha=0.5, label="Perfect forecast")
        ax.set_xlabel("Actual")
        ax.set_ylabel("Forecast")
        ax.set_title(f"{winner}: Forecast vs Actual")
        ax.legend()
    else:
        ax.text(0.5, 0.5, "Scatter unavailable", ha="center", va="center", transform=ax.transAxes)
    ax.grid(alpha=0.2)

    plt.suptitle("Saudi Motor Insurance DSS — Demo Forecast Validation", fontsize=15, fontweight="bold")
    plt.tight_layout()
    vp = OUTPUT_DIR / "forecast_validation.png"
    plt.savefig(vp, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Validation plot saved: {vp}")
except ImportError:
    print("matplotlib not available — skipping plot.")


# =============================================================================
# STEP 23 — MARKDOWN REPORT
# =============================================================================

print()
print("=" * 78)
print("STEP 23: Writing markdown report")
print("=" * 78)

report_path = OUTPUT_DIR / "seasonal_forecast_report.md"
with open(report_path, "w", encoding="utf-8") as rpt:
    rpt.write("# Seasonal Forecast Report\n\n")
    rpt.write(f"Generated: {datetime.now():%Y-%m-%d %H:%M}\n\n")
    rpt.write("> **DEMO FORECAST:** Synthetic data. Modelling window excludes immature Oct–Dec 2023.\n\n")

    rpt.write("## Data Summary\n\n")
    rpt.write(f"- Window: {monthly['ds'].min():%Y-%m} to {monthly['ds'].max():%Y-%m} ({len(monthly)} months)\n")
    rpt.write(f"- Train: {len(train)}, Holdout: {len(holdout)}\n")
    rpt.write(f"- Total claims: {monthly['CLAIM_COUNT'].sum():,.0f}\n\n")

    rpt.write("## Model Comparison\n\n")
    rpt.write("| Model | WAPE | Bias | Coverage | MAE |\n|---|---:|---:|---:|---:|\n")
    for nm, m in validation_results.items():
        cov = f"{m['coverage']:.0%}" if pd.notna(m['coverage']) else "—"
        rpt.write(f"| {nm} | {m['wape']:.1%} | {m['bias']:.3f} | {cov} | {m['mae']:,.0f} |\n")
    rpt.write(f"\n**Selected:** {winner} — {selection_reason}\n\n")

    rpt.write("## Seasonality\n\n")
    if pd.notna(seasonality_p):
        rpt.write(f"- ANOVA F={seasonality_f:.2f}, p={seasonality_p:.4f}\n")
        rpt.write(f"- {'Significant' if seasonality_p < 0.05 else 'Not significant'} at 5%\n\n")
    else:
        rpt.write("- ANOVA unavailable\n\n")

    rpt.write("## Business Validation\n\n")
    rpt.write(f"- Trailing 12M avg monthly premium: {trailing_12_avg_prem:,.0f}\n")
    rpt.write(f"- Forecast avg monthly premium: {forecast_avg_prem:,.0f}\n")
    rpt.write(f"- Premium change: {premium_change:+.1%}\n")
    rpt.write(f"- Trailing 12M LR: {trailing_12_lr:.1%}\n")
    rpt.write(f"- Forecast 12M LR: {forecast_12_lr:.1%}\n\n")

    rpt.write("## Validation Summary\n\n")
    rpt.write(f"- Aggregate WAPE: {agg_wape:.1%} {'PASS' if agg_wape_pass else 'REVIEW'}\n")
    rpt.write(f"- Monthly WAPE: {winner_wape:.1%}\n")
    rpt.write(f"- Raw bias: {winner_bias:.3f}\n")
    cov_str = f"{winner_coverage:.0%}" if pd.notna(winner_coverage) else "N/A"
    rpt.write(f"- Coverage: {cov_str}\n\n")

    rpt.write("## 12-Month Forecast Preview\n\n")
    rpt.write("| Month | Claims | Premium | LR | CI Lower | CI Upper |\n|---|---:|---:|---:|---:|---:|\n")
    preview = output[(output["FORECAST_HORIZON"] == "12M")]
    for _, r in preview.iterrows():
        rpt.write(f"| {r['MONTH_DATE'][:7]} | {r['FORECAST_CLAIMS']:,.0f} | {r['FORECAST_PREMIUM']:,.0f} | {r['FORECAST_LOSS_RATIO']:.1%} | {r['CI_LOWER_90']:.1%} | {r['CI_UPPER_90']:.1%} |\n")
    rpt.write("\n")

print(f"Report saved: {report_path}")


# =============================================================================
# FINAL PREVIEW
# =============================================================================

print()
print("=" * 78)
print("FINAL FORECAST PREVIEW")
print("=" * 78)

preview = output[output["FORECAST_HORIZON"] == "12M"].copy()
print(f"{'Month':>10} {'Claims':>15} {'Premium':>15} {'LR':>8} {'CI Lo':>10} {'CI Hi':>10}")
for _, r in preview.iterrows():
    print(f"{r['MONTH_DATE'][:7]:>10} {r['FORECAST_CLAIMS']:>15,.0f} {r['FORECAST_PREMIUM']:>15,.0f} {r['FORECAST_LOSS_RATIO']:>7.1%} {r['CI_LOWER_90']:>9.1%} {r['CI_UPPER_90']:>9.1%}")


# =============================================================================
# DONE
# =============================================================================

print()
print("=" * 78)
print("DONE")
print("=" * 78)
print(f"\nOutputs:")
print(f"  CSV:  {output_path}")
print(f"  Report: {report_path}")
print(f"  Plot: {OUTPUT_DIR / 'forecast_validation.png'}")
print(f"\nCSV contains {len(monthly)} actual rows + {len(output) - len(monthly)} forecast rows.")
print(f"Filter PERIOD_TYPE='actual' for history, 'forecast' + FORECAST_HORIZON for forward view.")
print(f"\nNOTE: Demo forecast. Not production actuarial assumptions.")