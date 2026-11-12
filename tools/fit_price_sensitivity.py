"""
fit_price_sensitivity.py: estimate the optimizer's renewal parameters from real renewals.
Reads the totals that `tools/profile_renewal.py --price [--notices]` saved; needs no database.

    .venv/Scripts/python.exe tools/fit_price_sensitivity.py
    .venv/Scripts/python.exe tools/fit_price_sensitivity.py --min-priced 30

The optimizer (pipeline/09_profit_optimizer.py) uses
    P(renew) = 1 / (1 + exp(-(logit(P0) - k * price_change)))
with P0 = baseline_renewal and k = the slope per +100% price change (config.toml: 0.90 and 3 / 5 / 8).

Method 1, segment-years (retail; no offered price for customers who left): each segment
(office x policy type x class x source x vehicle age) in each expiry year has a renewal rate and the
average premium change its renewers got. A binomial GLM of renewal on that change, with segment and year
effects, measures k from how renewal moved when a segment's price moved more or less than usual.
Method 2, renewal notices (fleet / corporate): the offered premium is known for leavers too, so renewal
can be read directly by offered-change band.

Writes <raw_dir>/checks/price_sensitivity_fit.csv and prints the comparison with config.toml.
Limits are printed with the result: a segment's average change is measured on its renewers only, and
competitors' prices moving at the same time cannot be separated out.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from settings import S  # noqa: E402

PRICED = ["A <-20%", "B -20..-10%", "C -10..-3%", "D -3..+3%", "E +3..+10%", "F +10..+20%",
          "G +20..+40%", "H >+40%"]
SEG = ["OFFICE", "POLICY_TYPE", "CLASS", "SOURCE", "AGE_BAND"]


def logit(p):
    return np.log(p / (1 - p))


def segment_years(price: pd.DataFrame, min_priced: int) -> pd.DataFrame:
    """One row per segment x expiry year: vehicles, renewed, and the renewers' average premium change."""
    price = price.copy()
    for c in SEG:
        price[c] = price[c].astype(str)
    key = SEG + ["EXPIRY_YEAR"]
    tot = price.groupby(key)["VEHICLES"].sum().rename("VEHICLES")
    ren = price[price["CHANGE_BAND"] != "NOT_RENEWED"].groupby(key)["VEHICLES"].sum().rename("RENEWED")
    p = price[price["CHANGE_BAND"].isin(PRICED)].groupby(key)[["VEHICLES", "OLD_ANNUAL_PREM", "NEW_ANNUAL_PREM"]].sum()
    p = p.rename(columns={"VEHICLES": "PRICED"})
    sy = pd.concat([tot, ren, p], axis=1).fillna(0).reset_index()
    sy["RATE"] = sy["RENEWED"] / sy["VEHICLES"]
    sy["CHANGE"] = sy["NEW_ANNUAL_PREM"] / sy["OLD_ANNUAL_PREM"].replace(0, np.nan) - 1
    sy["SEGMENT"] = sy[SEG].agg("|".join, axis=1)
    return sy[(sy["PRICED"] >= min_priced) & sy["CHANGE"].between(-0.5, 1.0)]


def fit(sy: pd.DataFrame, formula: str) -> tuple[float, float, int]:
    """k (slope per +100% change, sign as in the optimizer) and its standard error."""
    m = smf.glm(formula, data=sy.assign(LEFT=sy["VEHICLES"] - sy["RENEWED"]),
                family=sm.families.Binomial()).fit(scale="X2")      # over-dispersion allowed
    return -m.params["CHANGE"], m.bse["CHANGE"], int(m.nobs)


def arc_elasticity(p0: float, k: float, pc: float = 0.10) -> float:
    p1 = 1 / (1 + np.exp(-(logit(p0) - k * pc)))
    return ((p1 - p0) / p0) / pc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--min-priced", type=int, default=30,
                    help="renewers with a price a segment-year needs before its average change is used")
    args = ap.parse_args()
    checks = (S.raw_dir or ROOT / "data" / "raw") / "checks"
    price = pd.read_csv(checks / "renewal_price.csv")
    opt = S.config["optimizer"]
    cases = opt["sensitivity_cases"]
    pd.set_option("display.width", 200)

    # What renewers paid: distribution of their premium change by year
    ren = price[price["CHANGE_BAND"].isin(PRICED)]
    dist = ren.pivot_table(index="EXPIRY_YEAR", columns="CHANGE_BAND", values="VEHICLES", aggfunc="sum").fillna(0)
    print("Premium change of renewers (share of renewers with a price, by expiry year):")
    print(dist.div(dist.sum(axis=1), axis=0).to_string(float_format="{:.1%}".format))
    allv = price.groupby("EXPIRY_YEAR")["VEHICLES"].sum()
    renv = price[price["CHANGE_BAND"] != "NOT_RENEWED"].groupby("EXPIRY_YEAR")["VEHICLES"].sum()
    chg = ren.groupby("EXPIRY_YEAR")[["NEW_ANNUAL_PREM", "OLD_ANNUAL_PREM"]].sum()
    print("\nBy expiry year:")
    print(pd.DataFrame({"VEHICLES": allv, "RENEWAL_RATE": renv / allv,
                        "RENEWERS_AVG_CHANGE": chg["NEW_ANNUAL_PREM"] / chg["OLD_ANNUAL_PREM"] - 1})
          .to_string(formatters={"RENEWAL_RATE": "{:.1%}".format, "RENEWERS_AVG_CHANGE": "{:+.1%}".format}))

    # Method 1
    sy = segment_years(price, args.min_priced)
    p0 = sy["RENEWED"].sum() / sy["VEHICLES"].sum()
    rows = []
    for name, formula in [("pooled (no controls)", "RENEWED + LEFT ~ CHANGE"),
                          ("year effects", "RENEWED + LEFT ~ CHANGE + C(EXPIRY_YEAR)"),
                          ("segment + year effects", "RENEWED + LEFT ~ CHANGE + C(EXPIRY_YEAR) + C(SEGMENT)")]:
        try:
            k, se, n = fit(sy, formula)
        except Exception as e:  # too few segment-years for a model
            print(f"  {name}: could not fit ({type(e).__name__}: {e})")
            continue
        rows.append({"METHOD": "segment-years", "MODEL": name, "K": k, "K_LOW": k - 1.96 * se,
                     "K_HIGH": k + 1.96 * se, "N_SEGMENT_YEARS": n, "VEHICLES": int(sy["VEHICLES"].sum()),
                     "BASE_RENEWAL": p0, "ARC_ELASTICITY_AT_10PCT": arc_elasticity(p0, k)})

    # Method 2
    notice_file = checks / "renewal_notice_price.csv"
    if notice_file.exists():
        nt = pd.read_csv(notice_file)
        bands = nt.groupby("OFFER_BAND")[["NOTICES", "RENEWED", "PRIOR_ANNUAL_PREM", "OFFER_ANNUAL_PREM"]].sum()
        bands["RENEWAL_RATE"] = bands["RENEWED"] / bands["NOTICES"]
        bands["AVG_OFFERED_CHANGE"] = (bands["OFFER_ANNUAL_PREM"] / bands["PRIOR_ANNUAL_PREM"].replace(0, np.nan) - 1)
        bands.loc[bands.index == "NO_PRIOR", "AVG_OFFERED_CHANGE"] = np.nan
        print("\nRenewal notices: renewal by offered premium change (fleet / corporate):")
        print(bands[["NOTICES", "RENEWAL_RATE", "AVG_OFFERED_CHANGE"]].to_string(
            formatters={"RENEWAL_RATE": "{:.1%}".format, "AVG_OFFERED_CHANGE": "{:+.1%}".format}))
        b = bands.drop(index="NO_PRIOR", errors="ignore").reset_index()
        b = b[(b["NOTICES"] >= 20) & b["AVG_OFFERED_CHANGE"].notna()]
        if len(b) >= 3:
            b = b.rename(columns={"AVG_OFFERED_CHANGE": "CHANGE", "NOTICES": "VEHICLES"})
            k, se, n = fit(b, "RENEWED + LEFT ~ CHANGE")
            pn = b["RENEWED"].sum() / b["VEHICLES"].sum()
            rows.append({"METHOD": "renewal notices", "MODEL": "offered change bands", "K": k,
                         "K_LOW": k - 1.96 * se, "K_HIGH": k + 1.96 * se, "N_SEGMENT_YEARS": n,
                         "VEHICLES": int(b["VEHICLES"].sum()), "BASE_RENEWAL": pn,
                         "ARC_ELASTICITY_AT_10PCT": arc_elasticity(pn, k)})

    res = pd.DataFrame(rows)
    res.to_csv(checks / "price_sensitivity_fit.csv", index=False)
    print(f"\nPrice sensitivity (k per +100% price change; config.toml uses low {cases['low']}, "
          f"base {cases['base']}, high {cases['high']} with baseline renewal {opt['baseline_renewal']:.0%}):")
    print(res.drop(columns="METHOD").to_string(index=False, formatters={
        "K": "{:.2f}".format, "K_LOW": "{:.2f}".format, "K_HIGH": "{:.2f}".format,
        "BASE_RENEWAL": "{:.1%}".format, "ARC_ELASTICITY_AT_10PCT": "{:+.2f}".format}))
    print("\nFor comparison, config.toml's arc elasticity at +10%: "
          + ", ".join(f"{c} {arc_elasticity(opt['baseline_renewal'], v):+.2f}" for c, v in cases.items()))
    print("\nLimits: a segment's change is measured on its renewers only (leavers' offers are unknown outside the "
          "notices), and market-wide price moves in the same year are not separated out. A k near 0 or negative "
          "means the data cannot see price sensitivity this way, not that customers do not react.")
    print(f"Saved {checks / 'price_sensitivity_fit.csv'}")


if __name__ == "__main__":
    main()
