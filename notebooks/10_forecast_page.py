"""
notebooks/10_build_forecast_page.py
===================================
Builds the three tables behind the Power BI "Portfolio Forecast" page.

Replaces the forecast half of 09_premium_vs_claims.py. Fixes carried in:
  1. Window truncation      — drops the earning ramp-up and the run-off tail
  2. Development to ultimate — chains paid dev factors onto immature quarters
  3. Quarterly grain         — monthly/fortnightly is noise at ~90 claims/quarter
  4. GLM forecast            — replaces np.polyfit on a level series
  5. Bias correction         — GLM under-predicts ~34% at 12m per the backtest
  6. PI from backtest WAPE   — with a floor so it can never collapse to zero

Outputs (to 02_facts/):
  fact_kpi_forecast.csv   one row per METRIC x HORIZON_KEY  -> the six KPI cards
  fact_timeseries.csv     long format, one row per period x METRIC -> the chart
  dim_metric.csv          six rows of display metadata -> generic DAX
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

BASE = Path(__file__).resolve().parent.parent
FACT_DIR = BASE / "SYNTHETIC_DSS_DATA" / "02_facts"
MODELS_DIR = BASE / "models"

# ── Window truncation ────────────────────────────────────────────────────
# Premium and claims end on DIFFERENT dates because they die for different
# reasons. Premium: policies are only written through 2024, so 2025+ is the
# mechanical run-off of expiring 2024 business, not a shrinking book.
# Claims: later accident quarters are too immature for dev factors to be credible.
START_Q = pd.Period("2009Q1", freq="Q")
PREMIUM_END_Q = pd.Period("2024Q4", freq="Q")
CLAIMS_END_Q = pd.Period("2023Q4", freq="Q")
VALUATION_DATE = pd.Timestamp("2025-01-31")

MOTOR_CLASSES = ["Motor Private", "Motor Commercial", "Motor Rent a Car"]

# ── Backtest-derived constants (outputs/backtest_report.md, claim_cost row) ──
# BIAS is signed: negative means the GLM under-predicts, so we divide by (1+bias).
# WAPE drives the prediction interval half-width.
HORIZONS = {
    "1Q": {"quarters": 1, "bias": -0.273, "wape": 0.516, "label": "1 Quarter"},
    "2Q": {"quarters": 2, "bias": -0.300, "wape": 0.480, "label": "2 Quarters"},
    "4Q": {"quarters": 4, "bias": -0.338, "wape": 0.440, "label": "4 Quarters"},
}
PI_FLOOR = 0.10  # never let the band collapse, however good the fit looks


def _log(m: str) -> None:
    print(f"[forecast_page] {m}", flush=True)


# =========================================================================
# Feature encoding (mirrors 06_pure_premium.transform_features)
# =========================================================================
def transform_features(df: pd.DataFrame, encoder_meta: dict) -> pd.DataFrame:
    feature_cols = list(encoder_meta["category_maps"].keys())
    df2 = df.copy()
    for col in feature_cols:
        kept = set(encoder_meta["category_maps"][col]["kept"])
        df2[col] = (
            df2[col].astype(str).replace({"nan": "Unknown"})
            .apply(lambda x: "Unknown" if x == "Unknown" else (x if x in kept else "Other"))
        )
    X = pd.get_dummies(df2[feature_cols], drop_first=True, dtype=int)
    for c in encoder_meta["columns"]:
        if c not in X.columns:
            X[c] = 0
    return X[encoder_meta["columns"]]


# =========================================================================
# Development to ultimate
# =========================================================================
def build_dev_ladder(dev_path: Path) -> pd.DataFrame:
    """
    Turn the transition factors into a lookup of
    (maturity_months -> factor still to apply to reach ultimate).
    """
    dev = pd.read_csv(dev_path)
    rows = []
    for _, r in dev.iterrows():
        frm, to = r["transition"].split("->")
        rows.append({"from_m": int(frm), "to_m": int(to), "factor": float(r["factor"])})
    ladder = pd.DataFrame(rows).sort_values("from_m").reset_index(drop=True)
    # Cumulative product from each maturity onward = remaining development
    ladder["to_ultimate"] = ladder["factor"][::-1].cumprod()[::-1]
    return ladder


def factor_to_ultimate(maturity_months: float, ladder: pd.DataFrame) -> float:
    """Remaining development factor for a cohort at the given maturity."""
    future = ladder[ladder["from_m"] >= maturity_months]
    if future.empty:
        return 1.0
    return float(future.iloc[0]["to_ultimate"])


# =========================================================================
# Quarterly actuals
# =========================================================================
def build_actuals(ladder: pd.DataFrame) -> pd.DataFrame:
    _log("Loading earned premium and claims")
    ep = pd.read_csv(FACT_DIR / "fact_earned_premium.csv", parse_dates=["MONTH_DATE"])
    cl = pd.read_csv(FACT_DIR / "fact_claim.csv", parse_dates=["ACC_DATE"])

    ep["Q"] = ep["MONTH_DATE"].dt.to_period("Q")
    cl["Q"] = cl["ACC_DATE"].dt.to_period("Q")

    premium = ep.groupby("Q")["EARNED_PREMIUM"].sum()
    # Exposure-years from days exposed — this is the frequency denominator
    exposure = ep.groupby("Q")["DAYS_IN_MONTH_EXPOSED"].sum() / 365.25
    reported = cl.groupby("Q")["ULTIMATE_CLAIM"].sum()
    counts = cl.groupby("Q").size()

    df = pd.DataFrame({
        "premium": premium,
        "exposure_years": exposure,
        "claims_reported": reported,
        "claim_count": counts,
    })

    # Truncate — premium and claims to their own end dates
    df = df.loc[START_Q:PREMIUM_END_Q].copy()
    claim_cols = ["claims_reported", "claim_count"]
    # Inside the claims window a missing group means the extract genuinely holds
    # no claims for that quarter (2015-2018 is nearly empty) — that is a real
    # zero. Outside the window it means "not yet mature" and must stay null.
    in_window = df.index <= CLAIMS_END_Q
    df.loc[in_window, claim_cols] = df.loc[in_window, claim_cols].fillna(0.0)
    df.loc[~in_window, claim_cols] = np.nan

    n_empty = int((df.loc[in_window, "claim_count"] == 0).sum())
    if n_empty:
        _log(f"  WARNING: {n_empty} of {in_window.sum()} quarters have zero claims "
             f"in the extract — severity is undefined there (sampling gap)")

    # Develop each accident quarter to ultimate
    dev_factors = []
    for q in df.index:
        maturity = (VALUATION_DATE - q.to_timestamp(how="end")).days / 30.44
        dev_factors.append(factor_to_ultimate(maturity, ladder))
    df["dev_factor"] = dev_factors
    df["claims_ultimate"] = df["claims_reported"] * df["dev_factor"]
    df["claim_count_ult"] = df["claim_count"] * df["dev_factor"]

    _log(f"  window {df.index.min()} to {df.index.max()} ({len(df)} quarters)")
    _log(f"  claims populated through {CLAIMS_END_Q}, dev factors "
         f"{df['dev_factor'].min():.3f}–{df['dev_factor'].max():.3f}")

    # ── Guards: these are what would have caught the zero-forecast bug ──
    assert df["premium"].notna().all(), "NaN in premium inside the window"
    recent_claims = df.loc[:CLAIMS_END_Q, "claims_ultimate"]
    assert recent_claims.notna().all(), "NaN in claims inside the claims window"
    assert (df["dev_factor"] >= 1.0).all(), "development factor below 1.0"

    return df


def add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """The six presented metrics, at quarterly grain."""
    df = df.copy()
    df["frequency"] = 100 * df["claim_count_ult"] / df["exposure_years"]
    # Severity is undefined, not zero, in a quarter with no claims
    df["severity"] = np.where(
        df["claim_count_ult"] > 0,
        df["claims_ultimate"] / df["claim_count_ult"].replace(0, np.nan),
        np.nan,
    )
    df["loss_ratio"] = df["claims_ultimate"] / df["premium"]
    df["uw_margin"] = df["premium"] - df["claims_ultimate"]
    return df


# =========================================================================
# GLM forecast
# =========================================================================
def glm_forecast_rates() -> dict:
    """
    Score the recent policy cohort to get portfolio-level expected frequency
    (per exposure-year) and expected severity (per claim), mix-weighted.
    """
    _log("Scoring recent cohort through the GLMs")
    with open(MODELS_DIR / "frequency_glm.pkl", "rb") as f:
        freq_b = pickle.load(f)
    with open(MODELS_DIR / "severity_glm.pkl", "rb") as f:
        sev_b = pickle.load(f)

    pol = pd.read_csv(FACT_DIR / "fact_policy.csv", low_memory=False)
    pol = pol[pol["CLASS_DESC"].isin(MOTOR_CLASSES)]
    pol = pol[pol["EXPOSURE_YEARS"] > 0]
    # Recent mix = the cohort we assume renews forward
    recent = pol[pol["POLICY_YEAR"] >= pol["POLICY_YEAR"].max() - 1].copy()
    _log(f"  cohort: {len(recent):,} policies from years "
         f"{recent['POLICY_YEAR'].min()}–{recent['POLICY_YEAR'].max()}")

    Xf = sm.add_constant(transform_features(recent, freq_b["encoder_meta"]),
                         has_constant="add")
    Xs = sm.add_constant(transform_features(recent, sev_b["encoder_meta"]),
                         has_constant="add")

    freq_per_year = freq_b["result"].predict(Xf, offset=np.zeros(len(Xf)))
    sev_per_claim = sev_b["result"].predict(Xs)

    w = recent["EXPOSURE_YEARS"].values
    freq_rate = float(np.average(freq_per_year, weights=w))
    sev_rate = float(np.average(sev_per_claim, weights=freq_per_year * w))

    _log(f"  expected frequency: {100*freq_rate:.2f} per 100 exposure-years")
    _log(f"  expected severity:  SAR {sev_rate:,.0f} per claim")
    return {"freq_per_year": freq_rate, "severity": sev_rate}


def build_forecast(actuals: pd.DataFrame, rates: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (kpi_table, forecast_timeseries)."""
    # Forward exposure and premium: hold the recent run-rate flat.
    # Documented assumption, not a trend extrapolation — the last quarters of
    # the window are partially run-off and a fitted trend would inherit that.
    recent_exposure = actuals["exposure_years"].tail(4).mean()
    recent_premium = actuals["premium"].tail(4).mean()

    last_q = actuals.index.max()
    kpi_rows, ts_rows = [], []

    for key, cfg in HORIZONS.items():
        n = cfg["quarters"]
        bias, wape = cfg["bias"], cfg["wape"]

        exposure = recent_exposure * n
        premium = recent_premium * n

        # Raw GLM expectation, then bias-correct against the backtest
        claim_count_raw = rates["freq_per_year"] * exposure
        claims_raw = claim_count_raw * rates["severity"]
        claims = claims_raw / (1 + bias)
        claim_count = claim_count_raw / (1 + bias)

        frequency = 100 * claim_count / exposure
        severity = claims / claim_count
        loss_ratio = claims / premium
        uw_margin = premium - claims

        # Reference = the equivalent trailing actual window
        ref = actuals.dropna(subset=["claims_ultimate"]).tail(n)
        ref_claims = ref["claims_ultimate"].sum()
        ref_premium = ref["premium"].sum()
        ref_count = ref["claim_count_ult"].sum()
        ref_exposure = ref["exposure_years"].sum()

        half = max(wape, PI_FLOOR)

        metrics = {
            # metric        forecast     reference                banded?
            "claims_cost":  (claims,     ref_claims,              True),
            "frequency":    (frequency,  100 * ref_count / ref_exposure, True),
            "severity":     (severity,   ref_claims / ref_count,  True),
            "premium":      (premium,    ref_premium,             False),
            "loss_ratio":   (loss_ratio, ref_claims / ref_premium, True),
            "uw_margin":    (uw_margin,  ref_premium - ref_claims, True),
        }

        for metric, (fval, rval, banded) in metrics.items():
            # Premium is far more predictable than claims and must not inherit
            # claims uncertainty — leave its band null.
            lo = fval * (1 - half) if banded else np.nan
            hi = fval * (1 + half) if banded else np.nan
            kpi_rows.append({
                "HORIZON_KEY": key,
                "HORIZON_LABEL": cfg["label"],
                "HORIZON_ORDER": n,
                "METRIC": metric,
                "FORECAST_VALUE": fval,
                "REFERENCE_VALUE": rval,
                "PI_LOWER": lo,
                "PI_UPPER": hi,
                "ZONE": "All", "CLASS_DESC": "All", "POLICY_TYPE_DESC": "All",
            })

        # Chart series only needs the default horizon path.
        # Each metric forecasts from its OWN last actual, because claims and
        # premium actuals end on different dates. Anchoring both to the premium
        # window would leave a visible hole in the claims line.
        if key == "4Q":
            for metric, origin, val, banded in [
                ("claims_cost", CLAIMS_END_Q, claims / n, True),
                ("premium", PREMIUM_END_Q, premium / n, False),
            ]:
                for i in range(1, n + 1):
                    grow = half * (1 + 0.25 * np.log(1 + i))  # widens with horizon
                    ts_rows.append({
                        "PERIOD_DATE": (origin + i).to_timestamp(),
                        "PERIOD_TYPE": "forecast",
                        "METRIC": metric, "VALUE": val,
                        "PI_LOWER": val * (1 - grow) if banded else np.nan,
                        "PI_UPPER": val * (1 + grow) if banded else np.nan,
                        "ZONE": "All", "CLASS_DESC": "All", "POLICY_TYPE_DESC": "All",
                    })

    return pd.DataFrame(kpi_rows), pd.DataFrame(ts_rows)


def build_timeseries(actuals: pd.DataFrame, fc: pd.DataFrame) -> pd.DataFrame:
    """Long format: one row per period x metric. Drives the toggle with no DAX."""
    rows = []
    for q, r in actuals.iterrows():
        for metric, col in [("claims_cost", "claims_ultimate"), ("premium", "premium")]:
            if pd.notna(r[col]):
                rows.append({
                    "PERIOD_DATE": q.to_timestamp(), "PERIOD_TYPE": "actual",
                    "METRIC": metric, "VALUE": r[col],
                    "PI_LOWER": np.nan, "PI_UPPER": np.nan,
                    "ZONE": "All", "CLASS_DESC": "All", "POLICY_TYPE_DESC": "All",
                })
    hist = pd.DataFrame(rows)

    # Bridge row: repeat the last actual as the first forecast point so the
    # solid and dashed lines connect instead of jumping.
    bridge = []
    for metric in ["claims_cost", "premium"]:
        m = hist[hist["METRIC"] == metric]
        if not m.empty:
            last = m.sort_values("PERIOD_DATE").iloc[-1]
            b = last.copy()
            b["PERIOD_TYPE"] = "forecast"
            b["PI_LOWER"] = b["PI_UPPER"] = last["VALUE"]
            bridge.append(b)

    out = pd.concat([hist, pd.DataFrame(bridge), fc], ignore_index=True)
    return out.sort_values(["METRIC", "PERIOD_DATE", "PERIOD_TYPE"])


def build_dim_metric() -> pd.DataFrame:
    """
    DIRECTION: +1 where higher is good, -1 where higher is bad. Drives colour.
    DELTA_TYPE: loss_ratio moves in percentage POINTS, not percent change.
                68% -> 71% is +3 pts, not +4.4%.
    """
    return pd.DataFrame([
        {"METRIC": "claims_cost", "DISPLAY_NAME": "Expected Claims",
         "FORMAT_TYPE": "currency", "DIRECTION": -1, "DELTA_TYPE": "pct", "SORT_ORDER": 1},
        {"METRIC": "frequency", "DISPLAY_NAME": "Expected Frequency",
         "FORMAT_TYPE": "rate", "DIRECTION": -1, "DELTA_TYPE": "pct", "SORT_ORDER": 2},
        {"METRIC": "severity", "DISPLAY_NAME": "Expected Severity",
         "FORMAT_TYPE": "currency", "DIRECTION": -1, "DELTA_TYPE": "pct", "SORT_ORDER": 3},
        {"METRIC": "premium", "DISPLAY_NAME": "Expected Premium",
         "FORMAT_TYPE": "currency", "DIRECTION": 1, "DELTA_TYPE": "pct", "SORT_ORDER": 4},
        {"METRIC": "loss_ratio", "DISPLAY_NAME": "Expected Loss Ratio",
         "FORMAT_TYPE": "percent", "DIRECTION": -1, "DELTA_TYPE": "pts", "SORT_ORDER": 5},
        {"METRIC": "uw_margin", "DISPLAY_NAME": "Expected U/W Margin",
         "FORMAT_TYPE": "currency", "DIRECTION": 1, "DELTA_TYPE": "pct", "SORT_ORDER": 6},
    ])


def main() -> None:
    print("=" * 64)
    print("Portfolio Forecast page — fact table builder")
    print("=" * 64)

    ladder = build_dev_ladder(FACT_DIR / "fact_dev_factors_paid.csv")
    actuals = add_metrics(build_actuals(ladder))
    rates = glm_forecast_rates()
    kpi, fc_ts = build_forecast(actuals, rates)
    ts = build_timeseries(actuals, fc_ts)
    dim = build_dim_metric()

    for name, df in [("fact_kpi_forecast", kpi), ("fact_timeseries", ts),
                     ("dim_metric", dim)]:
        path = FACT_DIR / f"{name}.csv"
        df.to_csv(path, index=False, encoding="utf-8")
        _log(f"wrote {name}.csv ({len(df)} rows)")

    print("\n--- KPI cards, 4Q horizon ---")
    v = kpi[kpi["HORIZON_KEY"] == "4Q"].merge(dim, on="METRIC").sort_values("SORT_ORDER")
    for _, r in v.iterrows():
        if r["FORMAT_TYPE"] == "percent":
            f, ref = f"{r['FORECAST_VALUE']:.1%}", f"{r['REFERENCE_VALUE']:.1%}"
            d = f"{(r['FORECAST_VALUE']-r['REFERENCE_VALUE'])*100:+.1f} pts"
        elif r["FORMAT_TYPE"] == "rate":
            f, ref = f"{r['FORECAST_VALUE']:.2f}", f"{r['REFERENCE_VALUE']:.2f}"
            d = f"{(r['FORECAST_VALUE']/r['REFERENCE_VALUE']-1):+.1%}"
        elif r["METRIC"] == "severity":
            f, ref = f"{r['FORECAST_VALUE']:,.0f}", f"{r['REFERENCE_VALUE']:,.0f}"
            d = f"{(r['FORECAST_VALUE']/r['REFERENCE_VALUE']-1):+.1%}"
        else:
            f, ref = f"{r['FORECAST_VALUE']/1e6:,.2f}M", f"{r['REFERENCE_VALUE']/1e6:,.2f}M"
            d = f"{(r['FORECAST_VALUE']/r['REFERENCE_VALUE']-1):+.1%}"
        print(f"  {r['DISPLAY_NAME']:<22} {f:>12}   vs actual {ref:>12}   {d:>10}")
    print("=" * 64)


if __name__ == "__main__":
    main()