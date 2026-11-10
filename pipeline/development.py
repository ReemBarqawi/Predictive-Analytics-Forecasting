"""
pipeline/development.py
=======================
Claims development shared by the forecast (step 07) and reserving (step 14), so the development
pattern is worked out in one place.

Input: a warehouse triangle of increments by accident quarter x development quarter
(sql/claims_dev_*.sql: INCURRED_CHANGE for cost, CLAIMS_REPORTED for counts).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Method constant: chain-ladder factors are volume-weighted over the latest 20 quarterly
# diagonals (5 years), so the current claims process and mix drive them, not 2008 behaviour.
DEV_DIAGONALS = 20

TRIANGLES = {"CLAIMS_COST": ("claims_dev_incurred", "INCURRED_CHANGE"),
             "CLAIM_COUNT": ("claims_dev_reported", "CLAIMS_REPORTED")}


def valuation_quarter(data_end: pd.Timestamp) -> pd.Period:
    """The last accident quarter the data covers to its end."""
    q_end = data_end.to_period("Q")
    return q_end if data_end.normalize() >= q_end.end_time.normalize() else q_end - 1


def cumulative(tri: pd.DataFrame, value_col: str, last_q: pd.Period):
    """Cumulative triangle (accident quarter x age) as known at the end of `last_q`.
    Returns (accident quarters, cumulative array, age in quarters of each accident quarter)."""
    aq = pd.to_datetime(tri["ACC_QUARTER"]).dt.to_period("Q")
    cq = pd.Series([a + int(d) for a, d in zip(aq, tri["DEV_QUARTER"])], index=tri.index)
    t = tri.assign(AQ=aq, CQ=cq)
    first_eval = t["CQ"].min()                     # history starts here: earlier accident
    t = t[(t["CQ"] <= last_q) & (t["AQ"] >= first_eval)]  # quarters lack their early development
    aqs = pd.period_range(first_eval, last_q, freq="Q")
    n = len(aqs)
    inc = (t.groupby(["AQ", "DEV_QUARTER"])[value_col].sum().unstack(fill_value=0)
             .reindex(index=aqs, columns=range(n), fill_value=0))
    age = np.array([(last_q - a).n for a in aqs])
    return aqs, inc.cumsum(axis=1).to_numpy(dtype=float), age


def cdf(cum: np.ndarray, age: np.ndarray) -> np.ndarray:
    """Volume-weighted chain-ladder: cumulative factor to ultimate by age (index 0 = latest)."""
    n = len(age)
    factors = np.ones(n - 1)
    for k in range(n - 1):
        rows = np.where(age >= k + 1)[0][-DEV_DIAGONALS:]
        den = cum[rows, k].sum()
        if den > 0:
            factors[k] = cum[rows, k + 1].sum() / den
    return np.append(np.cumprod(factors[::-1])[::-1], 1.0)


def reported_share(tri: pd.DataFrame, value_col: str, last_q: pd.Period) -> pd.Series:
    """Share of an accident quarter's ultimate already recorded at `last_q`, by accident quarter
    (1 / factor to ultimate). Accident quarters older than the triangle's history count as fully
    recorded; quarters after `last_q` are not in the index (nothing recorded yet)."""
    aqs, cum, age = cumulative(tri, value_col, last_q)
    return pd.Series(1.0 / cdf(cum, age)[age], index=aqs)
