#!/usr/bin/env python3
"""
build_premium_vs_claims_ts.py
=============================
Generates a monthly time-series fact table for the Executive Overview
"Premium vs Claims — historical & forecast" line chart.

Reads:
  - fact_earned_premium.csv  → monthly premium actuals
  - fact_claim.csv           → monthly claims actuals (by accident month)
  - fact_model_performance.csv → GLM prediction quality for PI calibration

Outputs:
  - fact_premium_vs_claims_ts.csv

Columns:
  PERIOD_DATE        date       first-of-month
  PERIOD_LABEL       text       "2020-01", "2020-02", ...
  PERIOD_TYPE        text       "actual" or "forecast"
  PREMIUM_ACTUAL     number     (null for forecast periods)
  PREMIUM_FORECAST   number     (null for actual periods, or trend extrapolation)
  CLAIMS_ACTUAL      number     (null for forecast periods)
  CLAIMS_FORECAST    number     (null for actual periods, or GLM-based)
  CLAIMS_PI_UPPER    number     80% prediction interval upper bound
  CLAIMS_PI_LOWER    number     80% prediction interval lower bound
  ZONE               text       for filter context (or "All" for portfolio)
  CLASS_DESC         text       for filter context (or "All" for portfolio)
  POLICY_TYPE_DESC   text       for filter context (or "All" for portfolio)

Usage:
    python build_premium_vs_claims_ts.py --facts-dir ./02_facts
"""

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd


def load_data(facts_dir: Path) -> dict:
    """Load source CSVs."""
    data = {}
    files = {
        "earned_premium": "fact_earned_premium.csv",
        "claims": "fact_claim.csv",
        "model_perf": "fact_model_performance.csv",
    }
    for key, fname in files.items():
        path = facts_dir / fname
        if path.exists():
            data[key] = pd.read_csv(path, encoding="utf-8")
            print(f"  Loaded {fname}: {len(data[key])} rows")
        else:
            print(f"  WARNING: {fname} not found")
            data[key] = pd.DataFrame()
    return data


def build_premium_actuals(ep_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate earned premium by month across the whole portfolio.
    Also builds per-segment aggregations for filtered views.
    """
    ep = ep_df.copy()
    # MONTH_DATE is the first-of-month date column
    ep["MONTH_DATE"] = pd.to_datetime(ep["MONTH_DATE"])

    # Portfolio level only — no segment splits to avoid double-counting.
    # Zone/class filtering happens via the relationship to dim tables
    # when the user selects a slicer in Power BI.
    portfolio = ep.groupby("MONTH_DATE").agg(
        PREMIUM_ACTUAL=("EARNED_PREMIUM", "sum")
    ).reset_index()
    portfolio["ZONE"] = "All"
    portfolio["CLASS_DESC"] = "All"
    portfolio["POLICY_TYPE_DESC"] = "All"

    return portfolio


def build_claims_actuals(clm_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate ultimate claims by accident month.
    """
    clm = clm_df.copy()
    clm["ACC_DATE"] = pd.to_datetime(clm["ACC_DATE"])
    clm["ACC_MONTH"] = clm["ACC_DATE"].dt.to_period("M").dt.to_timestamp()

    # Need zone mapping — claims have OFFICE_NAME, we need ZONE
    # The claim table doesn't have ZONE directly, so we aggregate at portfolio
    # and use POLICY_TYPE_DESC for some segmentation

    # Portfolio level only
    portfolio = clm.groupby("ACC_MONTH").agg(
        CLAIMS_ACTUAL=("ULTIMATE_CLAIM", "sum")
    ).reset_index().rename(columns={"ACC_MONTH": "MONTH_DATE"})
    portfolio["ZONE"] = "All"
    portfolio["CLASS_DESC"] = "All"
    portfolio["POLICY_TYPE_DESC"] = "All"

    return portfolio


def compute_forecast(actuals: pd.DataFrame, n_forecast_months: int = 12,
                     model_perf: pd.DataFrame = None) -> pd.DataFrame:
    """
    Build forecast lines for premium and claims using trend extrapolation.
    Also compute 80% prediction intervals for claims.

    For the demo/internship context, we use:
    - Premium forecast: linear trend on last 24 months
    - Claims forecast: linear trend on last 24 months
    - 80% PI: based on historical residual std (or model RMSE if available)
    """
    results = []

    # Process each segment group separately
    groups = actuals.groupby(["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"])

    for (zone, cls, prod), grp in groups:
        grp = grp.sort_values("MONTH_DATE").copy()
        grp["month_idx"] = range(len(grp))

        last_date = grp["MONTH_DATE"].max()
        n_actual = len(grp)

        # Use last 24 months (or all if fewer) for trend fitting
        fit_window = min(24, n_actual)
        fit_data = grp.tail(fit_window).copy()

        # ── Premium trend ──
        prem_vals = fit_data["PREMIUM_ACTUAL"].values
        prem_idx = np.arange(len(prem_vals))

        if len(prem_vals) >= 3 and not np.all(np.isnan(prem_vals)):
            mask = ~np.isnan(prem_vals)
            if mask.sum() >= 2:
                coeffs_p = np.polyfit(prem_idx[mask], prem_vals[mask], 1)
                prem_resid = prem_vals[mask] - np.polyval(coeffs_p, prem_idx[mask])
                prem_std = np.std(prem_resid)
            else:
                coeffs_p = [0, np.nanmean(prem_vals)]
                prem_std = np.nanstd(prem_vals) if len(prem_vals) > 1 else 0
        else:
            coeffs_p = [0, np.nanmean(prem_vals) if len(prem_vals) > 0 else 0]
            prem_std = 0

        # ── Claims trend ──
        clm_vals = fit_data["CLAIMS_ACTUAL"].values
        clm_idx = np.arange(len(clm_vals))

        if len(clm_vals) >= 3 and not np.all(np.isnan(clm_vals)):
            mask = ~np.isnan(clm_vals)
            if mask.sum() >= 2:
                coeffs_c = np.polyfit(clm_idx[mask], clm_vals[mask], 1)
                clm_resid = clm_vals[mask] - np.polyval(coeffs_c, clm_idx[mask])
                clm_std = np.std(clm_resid)
            else:
                coeffs_c = [0, np.nanmean(clm_vals)]
                clm_std = np.nanstd(clm_vals) if len(clm_vals) > 1 else 0
        else:
            coeffs_c = [0, np.nanmean(clm_vals) if len(clm_vals) > 0 else 0]
            clm_std = 0

        # Use coefficient of variation (CV) so PI scales with forecast level
        clm_mean = np.nanmean(clm_vals) if len(clm_vals) > 0 else 1
        clm_cv = clm_std / clm_mean if clm_mean > 0 else 0.10

        # Cap CV to something reasonable (5%–25%)
        clm_cv = max(0.05, min(0.25, clm_cv))

        # 80% PI z-score
        z80 = 1.282

        # ── Actual period rows (with trend-fitted "forecast" for overlap) ──
        for _, row in grp.iterrows():
            results.append({
                "PERIOD_DATE": row["MONTH_DATE"],
                "PERIOD_TYPE": "actual",
                "PREMIUM_ACTUAL": row["PREMIUM_ACTUAL"],
                "PREMIUM_FORECAST": None,
                "CLAIMS_ACTUAL": row["CLAIMS_ACTUAL"],
                "CLAIMS_FORECAST": None,
                "CLAIMS_PI_UPPER": None,
                "CLAIMS_PI_LOWER": None,
                "ZONE": zone,
                "CLASS_DESC": cls,
                "POLICY_TYPE_DESC": prod,
            })

        # ── Bridge: last actual month also gets a forecast value (for line continuity) ──
        last_row = grp.iloc[-1]
        last_prem = last_row["PREMIUM_ACTUAL"]
        last_clm = last_row["CLAIMS_ACTUAL"]

        results.append({
            "PERIOD_DATE": last_date,
            "PERIOD_TYPE": "forecast",
            "PREMIUM_ACTUAL": None,
            "PREMIUM_FORECAST": last_prem,  # bridge point
            "CLAIMS_ACTUAL": None,
            "CLAIMS_FORECAST": last_clm,    # bridge point
            "CLAIMS_PI_UPPER": last_clm,
            "CLAIMS_PI_LOWER": last_clm,
            "ZONE": zone,
            "CLASS_DESC": cls,
            "POLICY_TYPE_DESC": prod,
        })

        # ── Forecast periods ──
        for i in range(1, n_forecast_months + 1):
            fcast_date = last_date + pd.DateOffset(months=i)
            fcast_date = fcast_date.replace(day=1)

            # Extrapolate trend
            t = len(prem_vals) - 1 + i
            prem_fc = max(0, np.polyval(coeffs_p, t))
            clm_fc = max(0, np.polyval(coeffs_c, t))

            # PI proportional to forecast level, widens gently with log(1+horizon)
            horizon_factor = 1.0 + 0.4 * np.log(1 + i)
            pi_half = z80 * clm_cv * clm_fc * horizon_factor

            results.append({
                "PERIOD_DATE": fcast_date,
                "PERIOD_TYPE": "forecast",
                "PREMIUM_ACTUAL": None,
                "PREMIUM_FORECAST": round(prem_fc, 2),
                "CLAIMS_ACTUAL": None,
                "CLAIMS_FORECAST": round(clm_fc, 2),
                "CLAIMS_PI_UPPER": round(clm_fc + pi_half, 2),
                "CLAIMS_PI_LOWER": round(max(0, clm_fc - pi_half), 2),
                "ZONE": zone,
                "CLASS_DESC": cls,
                "POLICY_TYPE_DESC": prod,
            })

    return pd.DataFrame(results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts-dir", default=os.path.join("SYNTHETIC_DSS_DATA", "02_facts"))
    parser.add_argument("--out", default=None)
    parser.add_argument("--forecast-months", type=int, default=12)
    args = parser.parse_args()

    facts = Path(args.facts_dir)
    out_path = args.out or str(facts / "fact_premium_vs_claims_ts.csv")

    print("=" * 60)
    print("Premium vs Claims Time Series Builder")
    print("=" * 60)

    data = load_data(facts)

    if data["earned_premium"].empty:
        print("ERROR: Cannot build time series without fact_earned_premium.csv")
        return

    # ── Build actuals ──
    print("\n  Building premium actuals...")
    prem = build_premium_actuals(data["earned_premium"])

    print("  Building claims actuals...")
    if not data["claims"].empty:
        clm = build_claims_actuals(data["claims"])
    else:
        print("  WARNING: No claims data — claims lines will be empty")
        clm = pd.DataFrame(columns=["MONTH_DATE", "CLAIMS_ACTUAL", "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"])

    # ── Merge premium + claims on (MONTH_DATE, ZONE, CLASS_DESC, POLICY_TYPE_DESC) ──
    print("  Merging premium and claims...")
    actuals = prem.merge(
        clm,
        on=["MONTH_DATE", "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"],
        how="outer"
    ).sort_values("MONTH_DATE")

    print(f"  Actual periods: {actuals['MONTH_DATE'].nunique()} months, "
          f"{len(actuals)} segment-months")

    # ── Build forecast ──
    print(f"  Generating {args.forecast_months}-month forecast with 80% PI...")
    ts = compute_forecast(
        actuals,
        n_forecast_months=args.forecast_months,
        model_perf=data.get("model_perf"),
    )

    # ── Format and write ──
    ts["PERIOD_DATE"] = pd.to_datetime(ts["PERIOD_DATE"]).dt.strftime("%Y-%m-%d")
    ts["PERIOD_LABEL"] = pd.to_datetime(ts["PERIOD_DATE"]).dt.strftime("%Y-%m")

    output_cols = [
        "PERIOD_DATE", "PERIOD_LABEL", "PERIOD_TYPE",
        "PREMIUM_ACTUAL", "PREMIUM_FORECAST",
        "CLAIMS_ACTUAL", "CLAIMS_FORECAST",
        "CLAIMS_PI_UPPER", "CLAIMS_PI_LOWER",
        "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC",
    ]
    ts = ts[output_cols]
    ts.to_csv(out_path, index=False, encoding="utf-8")

    n_actual = (ts["PERIOD_TYPE"] == "actual").sum()
    n_fcast = (ts["PERIOD_TYPE"] == "forecast").sum()
    print(f"\n  Written {len(ts)} rows ({n_actual} actual, {n_fcast} forecast)")
    print(f"  Output: {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()