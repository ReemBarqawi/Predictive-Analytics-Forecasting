"""
pipeline/17_renewal.py
======================
Renewal rate per segment, measured from the data (plan item E1), for the optimizer (step 09).

[PROVISIONAL, NEEDS MANAGER REVIEW: docs/TASK_PLAN.md decision on retention] Built 2026-11-11.

A car renews when the same vehicle (VEHICLE_ID, a hash of the chassis number) is insured again
from RENEW_BEFORE_DAYS before to RENEW_AFTER_DAYS after its cover ends. The policy number cannot
be used: it changes at renewal (the 2023 move from Direct to Brokers changed the policy key of
almost every renewed car).

  Measured on   the latest 12 months of expiries that can be observed: cover ending up to
                RENEW_AFTER_DAYS before the data cut-off, so every renewal had time to appear
  Per segment   ZONE x CLASS x POLICY_TYPE (the optimizer's segments), counted in vehicles
  Credibility   classical: Z = min(1, sqrt(renewals / [credibility] full_standard_claims)); a
                segment is blended toward its class x policy type, and that toward the portfolio
  Fallback      no vehicle extract (sql/vehicles.sql) -> no file is written and the optimizer
                keeps [optimizer] baseline_renewal from config.toml

Output: <facts>/fact_renewal_rates.csv, one row per segment, plus the portfolio rate by expiry year
for the report (fact_renewal_history.csv).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from settings import S, current_year

SEG = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]
GROUP = ["CLASS_DESC", "POLICY_TYPE_DESC"]
# Method constants: the renewal window around the end of cover. Earlier starts are early renewals
# (3-9 points, 2026-11-10 gap check); later ones are customers who came back, not renewals.
RENEW_BEFORE_DAYS = 30
RENEW_AFTER_DAYS = 60
FULL_STANDARD = S.config["credibility"]["full_standard_claims"]


def _log(m: str) -> None:
    print(f"[renewal] {m}", flush=True)


def renewals(v: pd.DataFrame) -> pd.DataFrame:
    """Each vehicle-policy with whether the same car was insured again inside the window."""
    v = v.sort_values(["VEHICLE_ID", "COVER_START", "POLICY_YEAR"])
    nxt = v.groupby("VEHICLE_ID")["COVER_START"].shift(-1)
    gap = (nxt - v["COVER_END"]).dt.days
    return v.assign(RENEWED=gap.between(-RENEW_BEFORE_DAYS, RENEW_AFTER_DAYS).astype(int))


def credible(df: pd.DataFrame, parent_rate: pd.Series) -> pd.DataFrame:
    z = np.minimum(1.0, np.sqrt(df["RENEWED"] / FULL_STANDARD))
    own = df["RENEWED"] / df["EXPIRING"].replace(0, np.nan)
    return df.assign(OWN_RATE=own, CREDIBILITY_Z=z,
                     RENEWAL_RATE=(z * own.fillna(parent_rate) + (1 - z) * parent_rate))


def main() -> None:
    path = S.facts_dir / "fact_vehicle.parquet"
    if not path.exists():
        _log("no fact_vehicle.parquet (run the vehicle extract: 00_extract_oracle.py --only vehicles); "
             "the optimizer keeps [optimizer] baseline_renewal")
        (S.facts_dir / "fact_renewal_rates.csv").unlink(missing_ok=True)
        return
    cutoff, _ = current_year()
    v = pd.read_parquet(path, columns=["VEHICLE_ID", "POLICY_NO", "POLICY_YEAR", "COVER_START", "COVER_END",
                                       "GROSS_PREMIUM"])
    seg = pd.read_csv(S.facts_dir / "fact_policy.csv", usecols=["POLICY_NO", "POLICY_YEAR"] + SEG,
                      low_memory=False).drop_duplicates(["POLICY_NO", "POLICY_YEAR"])
    r = renewals(v).merge(seg, on=["POLICY_NO", "POLICY_YEAR"], how="inner")

    observable_end = cutoff - pd.Timedelta(days=RENEW_AFTER_DAYS)
    hist = r[r["COVER_END"] <= observable_end]
    history = hist.groupby(hist["COVER_END"].dt.year).agg(EXPIRING=("RENEWED", "size"), RENEWED=("RENEWED", "sum"))
    history["RENEWAL_RATE"] = history["RENEWED"] / history["EXPIRING"]
    history.rename_axis("EXPIRY_YEAR").reset_index().to_csv(S.facts_dir / "fact_renewal_history.csv",
                                                          index=False, encoding="utf-8")

    window_start = observable_end - pd.DateOffset(years=1)
    w = hist[hist["COVER_END"] > window_start]
    _log(f"expiries {window_start.date()} to {observable_end.date()} (observable up to the data cut-off "
         f"{cutoff.date()}): {len(w):,} vehicles, {w['RENEWED'].mean():.1%} renewed "
         f"(window -{RENEW_BEFORE_DAYS}/+{RENEW_AFTER_DAYS} days)")

    portfolio = w["RENEWED"].mean()
    agg = lambda d, by: d.groupby(by).agg(EXPIRING=("RENEWED", "size"), RENEWED=("RENEWED", "sum"),
                                          EXPIRING_PREMIUM=("GROSS_PREMIUM", "sum"),
                                          RENEWED_PREMIUM=("GROSS_PREMIUM", lambda s: s[w.loc[s.index, "RENEWED"] == 1].sum()))
    grp = agg(w, GROUP).reset_index()
    grp = credible(grp, pd.Series(portfolio, index=grp.index))
    out = agg(w, SEG).reset_index().merge(grp[GROUP + ["RENEWAL_RATE"]].rename(columns={"RENEWAL_RATE": "PARENT_RATE"}),
                                          on=GROUP, how="left")
    out["PARENT_RATE"] = out["PARENT_RATE"].fillna(portfolio)
    out = credible(out, out["PARENT_RATE"])
    out["RENEWAL_RATE"] = out["RENEWAL_RATE"].clip(0.01, 0.99)
    out["PREMIUM_RENEWAL_RATE"] = out["RENEWED_PREMIUM"] / out["EXPIRING_PREMIUM"].replace(0, np.nan)
    out["PORTFOLIO_RATE"] = portfolio
    out["WINDOW_START"], out["WINDOW_END"] = window_start.date().isoformat(), observable_end.date().isoformat()
    out.to_csv(S.facts_dir / "fact_renewal_rates.csv", index=False, encoding="utf-8")

    _log(f"wrote fact_renewal_rates.csv ({len(out)} segments); portfolio {portfolio:.1%}, segments "
         f"{out['RENEWAL_RATE'].min():.1%} to {out['RENEWAL_RATE'].max():.1%}")
    print(history.assign(RENEWAL_RATE=history["RENEWAL_RATE"].map("{:.1%}".format)).tail(6).to_string())
    print(grp[GROUP + ["EXPIRING", "RENEWED", "RENEWAL_RATE"]].assign(
        RENEWAL_RATE=grp["RENEWAL_RATE"].map("{:.1%}".format)).to_string(index=False))


if __name__ == "__main__":
    main()
