"""
pipeline/exposure.py
====================
Plan H1: the exposure the pricing GLMs learn from and price on, in car-years, shared by steps 01, 02, 03, 04
and 09 so every step counts it the same way.

  CARS        average cars on the policy over its term = car-years / policy years. From the vehicle extract
              (each car's own cover dates, so a fleet adding cars mid-term counts them for the days they were
              on cover). Policies without vehicle rows take the median of their class among the policies
              that have them; with no vehicle extract at all every policy counts as one car.
  CAR_YEARS   car-years over the whole term = CARS x policy years: what a price covers.

For fitting, only the car-years already run count (earned to the data's cut-off, or to a backtest cut-off),
and each accident quarter's car-years are weighted by the share of its claims already reported
(chain-ladder on the reported-count triangle, pipeline/development.py). The claims a GLM sees are the ones
recorded so far; the exposure is scaled to match instead of grossing up claim counts policy by policy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from development import TRIANGLES, reported_share, valuation_quarter
from settings import S

DAY = pd.Timedelta(days=1)
# Accident quarters at least this fully reported are treated as complete (saves work; changes nothing visible)
COMPLETE_SHARE = 0.9995


def car_counts(policies: pd.DataFrame, vehicles: pd.DataFrame | None) -> tuple[pd.Series, pd.Series]:
    """Average cars per policy over its term, and where the number came from ("vehicles", "class median",
    "one car"), aligned to `policies`."""
    if vehicles is None or vehicles.empty:
        return pd.Series(1.0, index=policies.index), pd.Series("one car", index=policies.index)
    key = ["POLICY_NO", "POLICY_YEAR"]
    terms = policies[key + ["POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE"]].drop_duplicates(key)
    v = vehicles[key + ["COVER_START", "COVER_END"]].merge(terms, on=key, how="inner")
    # a car is on cover only while the policy is: its own dates, inside the policy's
    start = v[["COVER_START", "POLICY_EFFECTIVE_DATE"]].max(axis=1)
    end = v[["COVER_END", "POLICY_EXPIRY_DATE"]].min(axis=1)
    v["DAYS"] = ((end - start) / DAY).clip(lower=0)
    car_days = v.groupby(key)["DAYS"].sum().rename("CAR_DAYS").reset_index()
    p = policies[key + ["CLASS_DESC", "EXPOSURE_DAYS"]].merge(car_days, on=key, how="left")
    cars = (p["CAR_DAYS"] / p["EXPOSURE_DAYS"]).where(p["CAR_DAYS"] > 0)
    median = cars.groupby(p["CLASS_DESC"]).transform("median")
    source = np.select([cars.notna(), median.notna()], ["vehicles", "class median"], "one car")
    out = cars.fillna(median).fillna(1.0)
    return pd.Series(out.to_numpy(), index=policies.index), pd.Series(source, index=policies.index)


def reported_count_share() -> pd.Series:
    """Share of each accident quarter's claim count reported by the data's end (index: accident quarter).
    Empty when there is no reported-count triangle (the claims are then taken as complete)."""
    name, col = TRIANGLES["CLAIM_COUNT"]
    path = S.raw_dir / f"{name}.parquet" if S.raw_dir else None
    if path is None or not path.exists():
        return pd.Series(dtype=float)
    claims = pd.read_csv(S.facts_dir / "fact_claim.csv", usecols=["ACC_DATE"], parse_dates=["ACC_DATE"])
    return reported_share(pd.read_parquet(path), col, valuation_quarter(claims["ACC_DATE"].max()))


def valuation_end() -> pd.Timestamp:
    """Last day the claims cover in full: the end of the data's valuation quarter. Exposure for fitting
    stops here and claims after it are left out, so both sides cover the same days."""
    claims = pd.read_csv(S.facts_dir / "fact_claim.csv", usecols=["ACC_DATE"], parse_dates=["ACC_DATE"])
    return valuation_quarter(claims["ACC_DATE"].max()).end_time.normalize() + DAY


def earned_car_years(policies: pd.DataFrame, end: pd.Timestamp, shares: pd.Series | None = None,
                     start: pd.Timestamp | None = None) -> np.ndarray:
    """Car-years of each policy in force in [start, end), optionally weighted by each accident quarter's
    reported share. `policies` needs POLICY_EFFECTIVE_DATE, POLICY_EXPIRY_DATE and CARS."""
    eff = policies["POLICY_EFFECTIVE_DATE"].to_numpy("datetime64[ns]")
    exp = policies["POLICY_EXPIRY_DATE"].to_numpy("datetime64[ns]")
    lo = eff if start is None else np.maximum(eff, np.datetime64(start, "ns"))
    hi = np.minimum(exp, np.datetime64(end, "ns"))
    days = np.clip((hi - lo) / np.timedelta64(1, "D"), 0, None)
    if shares is not None and len(shares):
        for q, share in shares[shares < COMPLETE_SHARE].items():
            q0, q1 = np.datetime64(q.start_time, "ns"), np.datetime64(q.end_time.normalize() + DAY, "ns")
            in_q = np.clip((np.minimum(hi, q1) - np.maximum(lo, q0)) / np.timedelta64(1, "D"), 0, None)
            days = days - (1.0 - share) * in_q
    return days / 365.25 * policies["CARS"].to_numpy(dtype=float)
