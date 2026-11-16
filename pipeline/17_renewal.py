"""
pipeline/17_renewal.py
======================
Renewal rate per segment, measured from the data (plan item E1), for the optimizer (step 09).

[PROVISIONAL, NEEDS MANAGER REVIEW: docs/TASK_PLAN.md decision on retention] Built 2026-11-11.

A car renews when the same vehicle (VEHICLE_ID, a hash of the chassis number) is insured again
from RENEW_BEFORE_DAYS before to RENEW_AFTER_DAYS after its cover ends. The policy number cannot
be used: it changes at renewal (the 2023 move from Direct to Brokers changed the policy key of
almost every renewed car).

  Periods       12-month periods of expiries ending at the latest observable date (cover ending up to
                RENEW_AFTER_DAYS before the data cut-off, so every renewal had time to appear), and the
                periods a year, two years ... before it
  Window        plan H9: how many of the latest periods are pooled is chosen by a backtest: each candidate
                in WINDOW_CHOICES predicts every segment's rate in each earlier period from the periods
                before it, scored by the expiry-weighted mean absolute error; the longest window within
                one standard error of the best wins (pooling more years is steadier unless it is clearly
                worse). Too little history to test: one period
  Per segment   ZONE x CLASS x POLICY_TYPE (the optimizer's segments), counted in vehicles
  Credibility   classical: Z = min(1, sqrt(renewals / [credibility] full_standard_claims)); a
                segment is blended toward its class x policy type, and that toward the portfolio
  Fallback      no vehicle extract (sql/vehicles.sql) -> no file is written and the optimizer
                keeps [optimizer] baseline_renewal from config.toml

Output: <facts>/fact_renewal_rates.csv, one row per segment; fact_renewal_history.csv (the portfolio rate
by expiry year); fact_renewal_window.csv (the window backtest).
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
WINDOW_CHOICES = (1, 2, 3, 5)      # 12-month periods of expiries pooled (plan H9)
MIN_TESTS = 2                      # periods a window must be tested on to compete
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


def segment_rates(w: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """Credibility-weighted renewal rate per segment from the expiries `w`: (segments, class x type, portfolio)."""
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
    return out, grp, portfolio


def choose_window(hist: pd.DataFrame, observable_end: pd.Timestamp) -> tuple[int, pd.DataFrame]:
    """Plan H9: the number of 12-month expiry periods to pool, by backtest (see the module notes)."""
    period = np.ceil((observable_end - hist["COVER_END"]).dt.days / 365.25).clip(lower=1).astype(int)
    h = hist.assign(PERIOD=period)                     # 1 = the latest 12 months, 2 = the year before, ...
    rows = []
    for target in sorted(h["PERIOD"].unique()):
        if target == 1:                                # the live window: nothing after it to learn from yet
            continue
        actual = h[h["PERIOD"] == target - 1]          # predict this period ...
        act = actual.groupby(SEG).agg(EXPIRING=("RENEWED", "size"), RENEWED=("RENEWED", "sum")).reset_index()
        for n in WINDOW_CHOICES:
            inputs = h[h["PERIOD"].between(target, target + n - 1)]     # ... from the n periods before it
            if inputs["PERIOD"].nunique() < n:
                continue
            pred, _, port = segment_rates(inputs)
            m = act.merge(pred[SEG + ["RENEWAL_RATE"]], on=SEG, how="left").fillna({"RENEWAL_RATE": port})
            err = float((m["EXPIRING"] * (m["RENEWAL_RATE"] - m["RENEWED"] / m["EXPIRING"]).abs()).sum()
                        / m["EXPIRING"].sum())
            rows.append({"PREDICTED_PERIOD": int(target - 1), "WINDOW_PERIODS": n, "ERROR": err,
                         "VEHICLES": int(m["EXPIRING"].sum())})
    table = pd.DataFrame(rows, columns=["PREDICTED_PERIOD", "WINDOW_PERIODS", "ERROR", "VEHICLES"])
    # compare the windows on the periods every competing window was tested on
    counts = table.groupby("WINDOW_PERIODS")["PREDICTED_PERIOD"].nunique()
    ok = [n for n in counts.index if counts[n] >= MIN_TESTS]
    if not ok:
        return 1, table
    common = set.intersection(*(set(table.loc[table["WINDOW_PERIODS"] == n, "PREDICTED_PERIOD"]) for n in ok))
    t = table[table["WINDOW_PERIODS"].isin(ok) & table["PREDICTED_PERIOD"].isin(common)]
    s = t.groupby("WINDOW_PERIODS")["ERROR"].agg(["mean", "std", "count"])
    s["se"] = s["std"].fillna(0) / np.sqrt(s["count"])
    best = s["mean"].idxmin()
    chosen = int(s.index[s["mean"] <= s.loc[best, "mean"] + s.loc[best, "se"]].max())
    table["CHOSEN"] = table["WINDOW_PERIODS"] == chosen
    table["COMPARED_ON"] = ", ".join(str(p) for p in sorted(common))
    return chosen, table


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

    years, table = choose_window(hist, observable_end)
    table.to_csv(S.facts_dir / "fact_renewal_window.csv", index=False, encoding="utf-8")
    if "COMPARED_ON" in table:
        common = [int(p) for p in str(table["COMPARED_ON"].iloc[0]).split(", ")]
        compared = table[table["PREDICTED_PERIOD"].isin(common)]
        compared = compared[compared.groupby("WINDOW_PERIODS")["PREDICTED_PERIOD"].transform("nunique") == len(common)]
        summary = compared.groupby("WINDOW_PERIODS")["ERROR"].mean()
        _log("window backtest (expiry-weighted mean absolute error of segment rates, on the periods all windows "
             "were tested on): " + ", ".join(f"{n} year{'s' if n > 1 else ''} {e:.1%}" for n, e in summary.items())
             + f" -> {years} year{'s' if years > 1 else ''}")
    window_start = observable_end - pd.DateOffset(years=years)
    w = hist[hist["COVER_END"] > window_start]
    _log(f"expiries {window_start.date()} to {observable_end.date()} (observable up to the data cut-off "
         f"{cutoff.date()}): {len(w):,} vehicles, {w['RENEWED'].mean():.1%} renewed "
         f"(window -{RENEW_BEFORE_DAYS}/+{RENEW_AFTER_DAYS} days)")

    out, grp, portfolio = segment_rates(w)
    out["PREMIUM_RENEWAL_RATE"] = out["RENEWED_PREMIUM"] / out["EXPIRING_PREMIUM"].replace(0, np.nan)
    out["PORTFOLIO_RATE"] = portfolio
    out["WINDOW_START"], out["WINDOW_END"] = window_start.date().isoformat(), observable_end.date().isoformat()
    out["WINDOW_YEARS"] = years
    out.to_csv(S.facts_dir / "fact_renewal_rates.csv", index=False, encoding="utf-8")

    _log(f"wrote fact_renewal_rates.csv ({len(out)} segments); portfolio {portfolio:.1%}, segments "
         f"{out['RENEWAL_RATE'].min():.1%} to {out['RENEWAL_RATE'].max():.1%}")
    print(history.assign(RENEWAL_RATE=history["RENEWAL_RATE"].map("{:.1%}".format)).tail(6).to_string())
    print(grp[GROUP + ["EXPIRING", "RENEWED", "RENEWAL_RATE"]].assign(
        RENEWAL_RATE=grp["RENEWAL_RATE"].map("{:.1%}".format)).to_string(index=False))


if __name__ == "__main__":
    main()
