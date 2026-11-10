"""
pipeline/14_reserving.py
========================
Claims to ultimate: the only place the pipeline estimates each segment's final claims cost
(docs/METRIC_STANDARD.md, layer 4). Step 05 reads it for every policy year, so all years are on
one method and comparable.

[PROVISIONAL, NEEDS ACTUARY REVIEW: docs/TASK_PLAN.md decision 6] Method chosen 2026-11-10.

Basis: the report's written (policy) year. For each segment x policy year:

    ultimate = recorded + premium x expected loss ratio x (1 - share expected to be recorded by now)

  - recorded: paid + case reserve on those policies (fact_claim), accidents up to the valuation
    quarter. Claims after it (the part-quarter the extract runs into) are counted in the
    "still to come" term instead, so nothing is counted twice.
  - share expected to be recorded: the policies' premium is earned month by month
    (fact_earned_premium); each accident quarter is 1 / its factor to ultimate recorded
    (pipeline/development.py, the same chain-ladder pattern as the forecast), and accident quarters
    after the valuation have nothing recorded yet. So "still to come" covers both late reporting
    and cover not yet run.
  - expected loss ratio (Bornhuetter-Ferguson a priori): Cape Cod for each class x policy type
    over the latest CAPE_COD_YEARS policy years (recorded / premium x share recorded), blended
    toward the portfolio's by classical credibility on its claim count ([credibility]
    full_standard_claims in config.toml). Nothing is typed in.
  - claim counts the same way, with the reported-count pattern and a claims-per-premium rate.

Backtest (Oracle, 2026-11-10; 5 past valuations, recorded-at-the-time rebuilt from the triangle's
pattern, compared with today's estimate of the same policy year): mean abs error BF 10.0%,
chain-ladder 7.4%, recorded only (the old rule for past years) 44.6%. BF lags when loss ratios turn
(2023 at 2023Q2: +39%); chain-ladder is unstable while a year is young (2022 at 2022Q2: +32%).
BF is kept as the headline, the plan's method for an immature year; ULTIMATE_CHAIN_LADDER is
written beside it so the range is visible (2024: BF 97.2% vs chain-ladder 113.3% loss ratio).

Without claim triangles (the synthetic source, whose ULTIMATE_CLAIM already includes IBNR) the
recorded figures are used as ultimate.

Output: <facts>/fact_claims_ultimate.csv, one row per ZONE x CLASS_DESC x POLICY_TYPE_DESC x POLICY_YEAR
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from settings import S, current_year
from development import TRIANGLES, reported_share, valuation_quarter

SEG = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]
KEYS = SEG + ["POLICY_YEAR"]
GROUP = ["CLASS_DESC", "POLICY_TYPE_DESC"]
# Method constant: the expected loss ratio is measured over the latest three policy years, recent
# enough to reflect today's book, long enough to be stable.
CAPE_COD_YEARS = 3
FULL_STANDARD = S.config["credibility"]["full_standard_claims"]


def _log(m: str) -> None:
    print(f"[reserving] {m}", flush=True)


def load(last_q: pd.Period | None):
    ep = pd.read_csv(S.facts_dir / "fact_earned_premium.csv",
                     usecols=KEYS + ["MONTH_DATE", "EARNED_PREMIUM"], parse_dates=["MONTH_DATE"])
    pol = pd.read_csv(S.facts_dir / "fact_policy.csv", usecols=["POLICY_NO", "POLICY_YEAR"] + SEG,
                      low_memory=False).drop_duplicates(["POLICY_NO", "POLICY_YEAR"])
    cl = pd.read_csv(S.facts_dir / "fact_claim.csv",
                     usecols=["POLICY_NO", "POLICY_YEAR", "CLM_NO", "ACC_DATE", "POLICY_MATCHED", "ULTIMATE_CLAIM"],
                     parse_dates=["ACC_DATE"], low_memory=False)
    cl = cl[cl["POLICY_MATCHED"].astype(str).str.lower() == "true"].merge(pol, on=["POLICY_NO", "POLICY_YEAR"])
    to_val = (pd.Series(True, index=cl.index) if last_q is None
              else cl["ACC_DATE"].dt.to_period("Q") <= last_q)
    cl = cl.assign(TO_VALUATION=to_val.astype(int), CLAIM_TO_VAL=cl["ULTIMATE_CLAIM"].where(to_val, 0.0))
    rec = cl.groupby(KEYS).agg(
        REPORTED_CLAIMS=("ULTIMATE_CLAIM", "sum"), REPORTED_CLAIM_COUNT=("CLM_NO", "size"),
        RECORDED_TO_VAL=("CLAIM_TO_VAL", "sum"), COUNT_TO_VAL=("TO_VALUATION", "sum"))
    return ep, rec


def expected_shares(ep: pd.DataFrame, shares: dict[str, pd.Series], last_q: pd.Period) -> pd.DataFrame:
    """Premium-weighted share of each segment x policy year's claims expected to be recorded by now."""
    aq = ep["MONTH_DATE"].dt.to_period("Q")
    out = ep.groupby(KEYS)["EARNED_PREMIUM"].sum().rename("WRITTEN_PREMIUM").to_frame()
    for col, share in shares.items():
        first = share.index.min()
        s = np.where(aq > last_q, 0.0,                                  # cover not yet run
                     np.where(aq < first, 1.0, share.reindex(aq).fillna(1.0).to_numpy()))
        out[f"SHARE_{col}"] = (ep["EARNED_PREMIUM"] * s).groupby([ep[k] for k in KEYS]).sum() / out["WRITTEN_PREMIUM"]
    return out.fillna({"SHARE_CLAIMS_COST": 1.0, "SHARE_CLAIM_COUNT": 1.0})


def expected_rates(df: pd.DataFrame, cur_year: int) -> pd.DataFrame:
    """Cape Cod rate (claims per unit of premium used up) per class x policy type, blended toward
    the portfolio by classical credibility on the window's recorded claim count."""
    w = df[(df["POLICY_YEAR"] <= cur_year) & (df["POLICY_YEAR"] > cur_year - CAPE_COD_YEARS)]
    rows, portfolio = [], {}
    for name, rec, share in (("ELR", "RECORDED_TO_VAL", "SHARE_CLAIMS_COST"),
                             ("ECR", "COUNT_TO_VAL", "SHARE_CLAIM_COUNT")):
        used = w["WRITTEN_PREMIUM"] * w[share]
        port = portfolio[name] = w[rec].sum() / used.sum()
        g = w.assign(USED=used).groupby(GROUP).agg(REC=(rec, "sum"), USED=("USED", "sum"),
                                                   N=("COUNT_TO_VAL", "sum"))
        z = np.minimum(1.0, np.sqrt(g["N"] / FULL_STANDARD))
        rows.append((z * (g["REC"] / g["USED"].replace(0, np.nan)).fillna(port) + (1 - z) * port).rename(name))
        if name == "ELR":
            rows.append(z.rename("CREDIBILITY_Z"))
            _log(f"expected loss ratio (Cape Cod, policy years {cur_year - CAPE_COD_YEARS + 1}-{cur_year}): "
                 f"portfolio {port:.1%}")
    out = pd.concat(rows, axis=1).reset_index()
    out.attrs["portfolio"] = portfolio
    return out


def main() -> None:
    cutoff, cur_year = current_year()
    paths = {c: S.raw_dir / f"{n}.parquet" for c, (n, _) in TRIANGLES.items()} if S.raw_dir else {}
    developed = bool(paths) and all(p.exists() for p in paths.values())
    last_q = valuation_quarter(cutoff) if developed else None
    ep, rec = load(last_q)

    if developed:
        shares = {c: reported_share(pd.read_parquet(paths[c]), TRIANGLES[c][1], last_q) for c in TRIANGLES}
        df = expected_shares(ep, shares, last_q).join(rec, how="left").fillna(0.0).reset_index()
        rates = expected_rates(df, cur_year)
        df = df.merge(rates, on=GROUP, how="left")
        # A class x policy type with no business in the window (e.g. an old product) takes the portfolio's
        df = df.fillna({**rates.attrs["portfolio"], "CREDIBILITY_Z": 0.0})
        p = df["WRITTEN_PREMIUM"]
        df["ULTIMATE_CLAIMS"] = np.maximum(
            df["REPORTED_CLAIMS"], df["RECORDED_TO_VAL"] + p * df["ELR"] * (1 - df["SHARE_CLAIMS_COST"]))
        df["ULTIMATE_CLAIM_COUNT"] = np.maximum(
            df["REPORTED_CLAIM_COUNT"], df["COUNT_TO_VAL"] + p * df["ECR"] * (1 - df["SHARE_CLAIM_COUNT"]))
        # Chain-ladder on the same basis, for the actuary's comparison only (not used by any page):
        # each year's own experience so far, grossed up by the share recorded.
        df["ULTIMATE_CHAIN_LADDER"] = np.maximum(
            df["REPORTED_CLAIMS"],
            df["RECORDED_TO_VAL"] / df["SHARE_CLAIMS_COST"].where(df["SHARE_CLAIMS_COST"] > 0)).fillna(df["ULTIMATE_CLAIMS"])
        df["METHOD"] = "Bornhuetter-Ferguson (Cape Cod)"
        _log(f"valuation {last_q} (claims to {cutoff.date()}); development pattern from the warehouse triangles")
    else:
        _log("no claim triangles for this source: recorded claims used as ultimate")
        df = ep.groupby(KEYS)["EARNED_PREMIUM"].sum().rename("WRITTEN_PREMIUM").to_frame() \
               .join(rec, how="left").fillna(0.0).reset_index()
        df["SHARE_CLAIMS_COST"] = df["SHARE_CLAIM_COUNT"] = 1.0
        df["ELR"] = df["ECR"] = df["CREDIBILITY_Z"] = np.nan
        df["ULTIMATE_CLAIMS"], df["ULTIMATE_CLAIM_COUNT"] = df["REPORTED_CLAIMS"], df["REPORTED_CLAIM_COUNT"]
        df["ULTIMATE_CHAIN_LADDER"] = df["ULTIMATE_CLAIMS"]
        df["METHOD"] = "Recorded (no triangles)"

    df["STILL_TO_COME"] = df["ULTIMATE_CLAIMS"] - df["REPORTED_CLAIMS"]
    df["VALUATION_DATE"] = cutoff.date().isoformat()
    cols = KEYS + ["WRITTEN_PREMIUM", "REPORTED_CLAIMS", "STILL_TO_COME", "ULTIMATE_CLAIMS",
                   "REPORTED_CLAIM_COUNT", "ULTIMATE_CLAIM_COUNT", "SHARE_CLAIMS_COST", "SHARE_CLAIM_COUNT",
                   "ELR", "ECR", "CREDIBILITY_Z", "ULTIMATE_CHAIN_LADDER", "METHOD", "VALUATION_DATE"]
    df = df[cols].sort_values(KEYS).reset_index(drop=True)

    # Checks: reported + still to come = ultimate; ultimate never below reported; nothing lost
    assert np.allclose(df["REPORTED_CLAIMS"] + df["STILL_TO_COME"], df["ULTIMATE_CLAIMS"])
    assert (df["STILL_TO_COME"] >= -1e-6).all(), "ultimate below reported"
    assert abs(df["REPORTED_CLAIMS"].sum() - rec["REPORTED_CLAIMS"].sum()) < 1.0, "recorded claims lost in the join"

    out = S.facts_dir / "fact_claims_ultimate.csv"
    df.to_csv(out, index=False, encoding="utf-8")
    _log(f"wrote {out.name} ({len(df)} rows)")
    y = df.groupby("POLICY_YEAR")[["WRITTEN_PREMIUM", "REPORTED_CLAIMS", "STILL_TO_COME", "ULTIMATE_CLAIMS"]].sum()
    y["SHARE_RECORDED"] = (df.assign(w=df["WRITTEN_PREMIUM"] * df["SHARE_CLAIMS_COST"]).groupby("POLICY_YEAR")["w"].sum()
                           / y["WRITTEN_PREMIUM"])
    y["LOSS_RATIO"] = y["ULTIMATE_CLAIMS"] / y["WRITTEN_PREMIUM"]
    money = ["WRITTEN_PREMIUM", "REPORTED_CLAIMS", "STILL_TO_COME", "ULTIMATE_CLAIMS"]
    y[money] = y[money] / 1e6
    print(y.round(3).to_string())


if __name__ == "__main__":
    main()
