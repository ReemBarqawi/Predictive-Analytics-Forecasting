"""
pipeline/25_lines_alerts.py
===========================
Management-alert inputs for the lines other than motor (plan F6), in motor's layout (step 11's
fact_alert_basis.csv), so the Management Alerts page and the email (step 15) cover every line.

Each other-lines book (department) goes through step 11's own calculation on its own, so its credibility
constant comes from its own segments and never moves motor's alerts:
  Earned premium   each kept policy's premium earned day by day over its cover, by month (step 20's kept policies,
                   zone from the office as their segment results)
  Expected claims  the pricing book's (step 24): premium x the class's credible developed loss ratio, over the term
  Claims           the kept claims at their incurred amount (paid + case reserve), by accident date; a claim counts
                   when it found its policy
A department without a pricing book (no loss ratio, e.g. Medical) gets no alerts.

Output: <facts>/fact_line_alert_basis.csv (step 11's columns + BOOK); removed when there are no other lines.
"""
from __future__ import annotations

import importlib

import numpy as np
import pandas as pd

from settings import S, current_year

SEG = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]
MAX_MONTHS = 36          # a cover of up to three years


def _log(m: str) -> None:
    print(f"[lines alerts] {m}", flush=True)


def monthly_earned(p: pd.DataFrame) -> pd.DataFrame:
    """Premium earned per policy and calendar month, in proportion to the days of cover in the month."""
    eff, exp = p["POLICY_EFFECTIVE_DATE"], p["POLICY_EXPIRY_DATE"]
    term = (exp - eff).dt.days.clip(lower=1).to_numpy(dtype=float)
    m0 = eff.dt.to_period("M")
    parts = []
    for k in range(MAX_MONTHS + 1):
        m = m0 + k
        start, end = m.dt.start_time, (m + 1).dt.start_time
        days = (np.minimum(exp, end) - np.maximum(eff, start)).dt.days.clip(lower=0).to_numpy(dtype=float)
        if not days.any():
            break
        keep = days > 0
        parts.append(pd.DataFrame({**{c: p.loc[keep, c].to_numpy() for c in SEG + ["POLICY_YEAR"]},
                                   "MONTH_DATE": m[keep].dt.to_timestamp().to_numpy(),
                                   "EARNED_PREMIUM": (p["TOTAL_PREMIUM"].to_numpy() * days / term)[keep]}))
    out = pd.concat(parts, ignore_index=True)
    return out.groupby(SEG + ["POLICY_YEAR", "MONTH_DATE"], as_index=False)["EARNED_PREMIUM"].sum()


def main() -> None:
    out_path = S.facts_dir / "fact_line_alert_basis.csv"
    book_path = S.facts_dir / "fact_line_policy_pricing.csv"
    if not book_path.exists():
        _log("no other-lines pricing book (step 24): nothing to do")
        out_path.unlink(missing_ok=True)
        return
    alerts = importlib.import_module("11_build_alerts")
    pricing = importlib.import_module("21_lines_pricing")
    kp, kc = pricing.kept()
    book = pd.read_csv(book_path, parse_dates=["POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE"])
    book["pure_premium_period"] = book["pure_premium_per_year"] * book["EXPOSURE_YEARS"]
    kc = kc.assign(ULTIMATE_CLAIM=kc["INCURRED"], POLICY_MATCHED=kc["MATCH"].ne("unmatched"))
    parts = []
    _, cy = current_year()
    for b, pp in book.groupby("BOOK"):
        p = kp[kp["DEPARTMENT"] == b]
        c = kc[kc["DEPARTMENT"] == b]
        # A book whose current year is not usable (step 20 left it out, e.g. FGA 2024) is judged on its latest
        # usable year, as every page does: its data stops at that year's end
        last_year = int(p.loc[p["POLICY_YEAR"] <= cy, "POLICY_YEAR"].max()) if (p["POLICY_YEAR"] <= cy).any() else None
        if last_year is None or last_year < cy - 1:
            _log(f"{b}: no usable year since {cy - 1}, no alerts")
            continue
        if last_year < cy:
            end = pd.Timestamp(last_year, 12, 31)
            p = p[p["POLICY_YEAR"] <= last_year]
            c = c[(c["ACC_DATE"] <= end) & (c["POLICY_YEAR"] <= last_year)]
            _log(f"{b}: {cy} not usable, judged on {last_year} (data to {end.date()})")
        if p.empty or c.empty:
            _log(f"{b}: no policies or claims, no alerts")
            continue
        _log(f"{b}:")
        basis = alerts.alert_basis(monthly_earned(p), pp[SEG + ["POLICY_NO", "POLICY_YEAR", "COMMISSION",
                                                                "pure_premium_period"]],
                                   c[["POLICY_NO", "POLICY_YEAR", "CLM_NO", "ACC_DATE", "ULTIMATE_CLAIM", "POLICY_MATCHED"]])
        parts.append(basis.assign(BOOK=b))
        _log(f"{b}: {len(basis)} segments, in-year premium {basis['EP_L12'].sum() / 1e6:,.1f}M, "
             f"claims {basis['CLAIMS_L12'].sum() / 1e6:,.1f}M ({basis['WINDOW_LABEL'].iloc[0] if len(basis) else ''})")
    if not parts:
        out_path.unlink(missing_ok=True)
        return
    pd.concat(parts, ignore_index=True).to_csv(out_path, index=False, encoding="utf-8")


if __name__ == "__main__":
    main()
