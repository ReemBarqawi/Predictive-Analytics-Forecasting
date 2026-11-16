"""
pipeline/24_lines_pricing_book.py
=================================
The other lines' pricing book: the same contract as motor's (step 03, fact_policy_pricing.csv), so the optimizer
(step 09) and the Optimal / Safe Zone / What-If / Repricing pages run every line through the same code.

The pricing-book contract (one row per policy-year; step 09 reads these columns):
  BOOK                          the book the optimizer prices on its own (its portfolio floor, its policy year):
                                motor = "Motor", here each department
  POLICY_NO, POLICY_YEAR, POLICY_EFFECTIVE_DATE, POLICY_EXPIRY_DATE
  ZONE, CLASS_DESC, POLICY_TYPE_DESC                      the optimizer's segment
  TOTAL_PREMIUM, charged_premium, COMMISSION, EXPOSURE_YEARS (years of cover)
  expected_frequency_per_year, expected_severity, pure_premium_per_year     expected claims per year of cover

How the other lines fill it:
  Policies      those step 20 kept (zone from the office, as their segment results)
  Claims level  premium x the class's credible loss ratio from step 21 (developed to ultimate, large claims
                capped with each class's excess loaded back, blended toward its department): the same loss
                ratio as its price indication. No trend is measured for these lines, so none is applied
  Claim count   premium x the class's expected claims per riyal (lines_development, Cape Cod with credibility)
  Inside a      flat: every policy of a segment carries its segment's rate (too few claims for a GLM); the
  segment       optimizer's risk percentile is then the same for all of them
  Renewal       the same client (CLIENT_NO) insured again in the class from RENEW_BEFORE_DAYS before to
                RENEW_AFTER_DAYS after the cover ends, per segment with credibility and the window chosen by
                backtest (step 17's own functions). Without CLIENT_NO in the extract: no rates are written and
                the optimizer uses [optimizer] baseline_renewal for these books (flagged in the run log)
  New business  next 12 months: step 22's forecast premium of the class less its expected renewals at no change,
                spread over the class's segments by premium; none when step 22 has no forecast for the class
  Not priced    a class without a loss ratio (no claims data, e.g. Medical) stays out of the book: its pages
                show blank

Basis: gross of reinsurance, as step 21 (the manager is still to decide gross or net).

Outputs: <facts>/fact_line_policy_pricing.csv, fact_line_renewal_rates.csv (with CLIENT_NO),
         fact_line_new_business.csv
"""
from __future__ import annotations

import importlib

import numpy as np
import pandas as pd

from lines_development import develop
from settings import S, current_year

SEG = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]
CLASS = ["DEPARTMENT", "CLASS_DESC"]
renewal = importlib.import_module("17_renewal")        # one renewal method for every book
RENEW_BEFORE_DAYS, RENEW_AFTER_DAYS = renewal.RENEW_BEFORE_DAYS, renewal.RENEW_AFTER_DAYS


def _log(m: str) -> None:
    print(f"[lines pricing book] {m}", flush=True)


def book(p: pd.DataFrame, pricing: pd.DataFrame, dev: pd.DataFrame) -> pd.DataFrame:
    """The pricing-book rows of the kept policies of every class with a loss ratio."""
    lr = pricing.set_index(CLASS)["CREDIBLE_LR"]
    ecr = dev.groupby(CLASS)["ECR"].first()
    b = p.join(lr, on=CLASS).join(ecr, on=CLASS)
    left_out = b.loc[b["CREDIBLE_LR"].isna(), "CLASS_DESC"].unique()
    if len(left_out):
        _log(f"not priced (no loss ratio): {', '.join(map(str, left_out))}")
    b = b[b["CREDIBLE_LR"].notna()].copy()
    b["EXPOSURE_YEARS"] = ((b["POLICY_EXPIRY_DATE"] - b["POLICY_EFFECTIVE_DATE"]).dt.days / 365.25).clip(lower=0)
    years = b["EXPOSURE_YEARS"].where(b["EXPOSURE_YEARS"] > 0)
    claims = b["TOTAL_PREMIUM"].clip(lower=0) * b["CREDIBLE_LR"]
    count = (b["TOTAL_PREMIUM"].clip(lower=0) * b["ECR"].fillna(0.0)).clip(lower=1e-9)
    b["expected_frequency_per_year"] = count / years
    b["expected_severity"] = claims / count
    b["pure_premium_per_year"] = claims / years
    b["charged_premium"] = b["TOTAL_PREMIUM"]
    b["BOOK"] = b["DEPARTMENT"]
    cols = ["BOOK", "DEPARTMENT", "POLICY_NO", "POLICY_YEAR", "POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE",
            "EXPOSURE_YEARS", "TOTAL_PREMIUM", "COMMISSION", "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC", "SUM_INSURED",
            "expected_frequency_per_year", "expected_severity", "pure_premium_per_year", "charged_premium"]
    return b[cols]


def renewal_rates(p: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame | None:
    """Per segment, like step 17 but with the client as the link. None without CLIENT_NO in the extract."""
    if "CLIENT_NO" not in p or p["CLIENT_NO"].isna().all():
        return None
    v = p.dropna(subset=["CLIENT_NO"]).assign(
        VEHICLE_ID=lambda d: d["CLIENT_NO"].astype(str) + "|" + d["CLASS_DESC"].astype(str),   # client within class
        COVER_START=p["POLICY_EFFECTIVE_DATE"], COVER_END=p["POLICY_EXPIRY_DATE"], GROSS_PREMIUM=p["TOTAL_PREMIUM"])
    r = renewal.renewals(v)
    observable_end = cutoff - pd.Timedelta(days=RENEW_AFTER_DAYS)
    hist = r[r["COVER_END"] <= observable_end]
    if hist.empty:
        return None
    out = []
    for dept, h in hist.groupby("DEPARTMENT"):
        years, _ = renewal.choose_window(h, observable_end)
        w = h[h["COVER_END"] > observable_end - pd.DateOffset(years=years)]
        if w.empty:
            continue
        seg, _, portfolio = renewal.segment_rates(w)
        out.append(seg.assign(BOOK=dept, PORTFOLIO_RATE=portfolio, WINDOW_YEARS=years))
        _log(f"renewal {dept}: {years} year(s) of expiries, {len(w):,} policies, {portfolio:.1%} renewed "
             f"(same client in the class, -{RENEW_BEFORE_DAYS}/+{RENEW_AFTER_DAYS} days)")
    return pd.concat(out, ignore_index=True) if out else None


def new_business(b: pd.DataFrame, rates: pd.DataFrame | None, baseline: float) -> pd.DataFrame:
    """Next 12 months' new business per segment: the class's forecast (step 22) less its expected renewals."""
    path = S.facts_dir / "fact_line_forecast.csv"
    if not path.exists():
        return pd.DataFrame(columns=SEG + ["NB_PREMIUM", "BOOK"])
    fc = pd.read_csv(path)
    fc = fc[(fc["FORECAST_HORIZON"] == "12M") & (fc["PERIOD_TYPE"] == "forecast")].groupby("CLASS_DESC")["FORECAST_PREMIUM"].sum()
    # the year the optimizer prices: each book's latest complete policy year (step 09's own rule)
    optimizer = importlib.import_module("09_profit_optimizer")
    priced = pd.concat([g[g["POLICY_YEAR"] == (optimizer.complete_years(g) or [g["POLICY_YEAR"].max()])[-1]]
                        for _, g in b.groupby("BOOK")])
    seg = priced.groupby(["BOOK"] + SEG)["charged_premium"].sum().rename("PREMIUM").reset_index()
    if rates is not None:
        seg = seg.merge(rates[["BOOK"] + SEG + ["RENEWAL_RATE"]], on=["BOOK"] + SEG, how="left")
    else:
        seg["RENEWAL_RATE"] = np.nan
    seg["RENEWAL_RATE"] = seg["RENEWAL_RATE"].fillna(baseline)
    seg["RENEWING"] = seg["PREMIUM"] * seg["RENEWAL_RATE"]
    cls = seg.groupby("CLASS_DESC")[["PREMIUM", "RENEWING"]].sum()
    cls["NB_TOTAL"] = (fc.reindex(cls.index) - cls["RENEWING"]).clip(lower=0).fillna(0.0)
    seg = seg.join(cls[["PREMIUM", "NB_TOTAL"]].rename(columns={"PREMIUM": "CLASS_PREMIUM"}), on="CLASS_DESC")
    seg["NB_PREMIUM"] = seg["NB_TOTAL"] * seg["PREMIUM"] / seg["CLASS_PREMIUM"].replace(0, np.nan)
    return seg[["BOOK"] + SEG + ["NB_PREMIUM"]].fillna({"NB_PREMIUM": 0.0})


def main() -> None:
    out = S.facts_dir / "fact_line_policy_pricing.csv"
    files = [out, S.facts_dir / "fact_line_renewal_rates.csv", S.facts_dir / "fact_line_new_business.csv"]
    pricing_path = S.facts_dir / "fact_line_pricing.csv"
    if not pricing_path.exists():
        _log("no fact_line_pricing.csv (step 21 found no other lines): nothing to do")
        for f in files:
            f.unlink(missing_ok=True)
        return
    cutoff, _ = current_year()
    pricing_step = importlib.import_module("21_lines_pricing")
    kp, kc = pricing_step.kept()
    dev = develop(kp, kc)
    b = book(kp, pd.read_csv(pricing_path), dev)
    b.to_csv(out, index=False, encoding="utf-8")
    rates = renewal_rates(kp, cutoff)
    if rates is None:
        files[1].unlink(missing_ok=True)
        _log("no CLIENT_NO in the other-lines extract (sql/lines_policies.sql): their renewal is not measured; "
             "the optimizer uses [optimizer] baseline_renewal for them")
    else:
        rates.to_csv(files[1], index=False, encoding="utf-8")
    baseline = float(S.config["optimizer"]["baseline_renewal"])
    nb = new_business(b, rates, baseline)
    nb.to_csv(files[2], index=False, encoding="utf-8")
    y = b.groupby(["BOOK", "POLICY_YEAR"]).agg(POLICIES=("POLICY_NO", "size"), PREMIUM=("charged_premium", "sum"))
    _log(f"{len(b):,} policy-years in {b['BOOK'].nunique()} books; latest years: " + "; ".join(
        f"{bk} {int(g.index.get_level_values('POLICY_YEAR').max())} {g['PREMIUM'].iloc[-1] / 1e6:,.1f}M"
        for bk, g in y.groupby(level="BOOK")) + f"; new business {nb['NB_PREMIUM'].sum() / 1e6:,.1f}M")


if __name__ == "__main__":
    main()
