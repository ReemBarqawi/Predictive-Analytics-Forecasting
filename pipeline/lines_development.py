"""
pipeline/lines_development.py
=============================
Claims to ultimate for the lines other than motor, the same method as motor's reserving (step 14):

    ultimate = recorded + premium x expected loss ratio x (1 - share expected to be recorded by now)

  - recorded: incurred (paid + case reserve) of the claims step 20 kept, accidents up to the valuation quarter;
    later ones (the part-quarter the extract runs into) are left to the "still to come" term
  - share expected to be recorded: each policy's premium is earned day by day over its cover; each accident
    quarter is recorded at 1 / its chain-ladder factor to ultimate (pipeline/development.py) from the department's
    own triangle (sql/lines_claims_dev_*.sql), or the pooled other-lines triangle when a department has fewer than
    MIN_TRIANGLE_CLAIMS reported claims; quarters after the valuation have nothing recorded yet. Only classes whose
    triangle adds up to their claims in the extract (within RECONCILE_TOLERANCE) build the patterns; the others
    are developed with them. A share above 1 (incurred falling as case reserves are released) counts as 1. So "still to
    come" covers late reporting and cover not yet run
  - expected loss ratio: Cape Cod per class over its latest CAPE_COD_YEARS usable policy years, blended toward the
    class's own Cape Cod over all its usable years, which is blended toward its department's; classical
    credibility on the claim count ([credibility] full_standard_claims) at each step
  - claim counts the same way, with the reported-count triangle

Without the triangles the recorded figures are the ultimate and the basis says "not developed".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from development import reported_share, valuation_quarter
from settings import S, current_year

CAPE_COD_YEARS = 3              # method constants, as step 14
MIN_TRIANGLE_CLAIMS = 200       # a department with fewer reported claims takes the pooled other-lines pattern
RECONCILE_TOLERANCE = 0.10      # a class's triangle total must be within this of its claims in the extract
TRIANGLES = {"COST": ("lines_claims_dev_incurred", "INCURRED_CHANGE"),
             "COUNT": ("lines_claims_dev_reported", "CLAIMS_REPORTED")}
KEY = ["DEPARTMENT", "CLASS_DESC", "ZONE", "POLICY_TYPE_DESC", "POLICY_YEAR"]


def _log(m: str) -> None:
    print(f"[lines development] {m}", flush=True)


def available() -> bool:
    return bool(S.raw_dir) and all((S.raw_dir / f"{n}.parquet").exists() for n, _ in TRIANGLES.values())


def _reconciled(tri: pd.DataFrame, col: str, measure: str) -> set:
    """Classes whose triangle total agrees with their claims in the extract (same accident period) within
    RECONCILE_TOLERANCE. A triangle that does not add up to its own claims is not a pattern for them."""
    claims = pd.read_parquet(S.facts_dir / "fact_line_claim.parquet", columns=["CLASS_NO", "ACC_DATE", "INCURRED", "CLM_NO"])
    claims = claims[claims["ACC_DATE"] >= pd.to_datetime(tri["ACC_QUARTER"]).min()]
    extract = (claims.groupby("CLASS_NO")["INCURRED"].sum() if measure == "COST"
               else claims.groupby("CLASS_NO")["CLM_NO"].size()).astype(float)
    total = tri.groupby("CLASS_NO")[col].sum()
    gap = (total - extract.reindex(total.index).fillna(0.0)).abs() / extract.reindex(total.index).abs().replace(0, np.nan)
    ok = set(gap[gap <= RECONCILE_TOLERANCE].index)
    for cls_no, g in gap.items():
        if cls_no not in ok:
            _log(f"{measure.lower()} triangle of class {cls_no} does not reconcile with its claims "
                 f"({total[cls_no]:,.0f} vs {extract.get(cls_no, 0):,.0f}): its claims take the reconciled classes' pattern")
    return ok


def _shares(last_q: pd.Period) -> tuple[dict[str, dict[str, pd.Series]], dict[str, dict[str, str]]]:
    """Reported share by accident quarter, per measure and department, and where each came from. Only classes whose
    triangle reconciles build a pattern; a department with fewer than MIN_TRIANGLE_CLAIMS reported claims in those
    takes the pooled pattern of every reconciled class."""
    out, source = {}, {}
    for measure, (name, col) in TRIANGLES.items():
        tri = pd.read_parquet(S.raw_dir / f"{name}.parquet")
        count_tri = pd.read_parquet(S.raw_dir / f"{TRIANGLES['COUNT'][0]}.parquet")
        ok = _reconciled(tri, col, measure)
        good = tri[tri["CLASS_NO"].isin(ok)]
        n = count_tri[count_tri["CLASS_NO"].isin(ok)].groupby("DEPARTMENT")["CLAIMS_REPORTED"].sum()
        out[measure] = {"__pooled__": reported_share(good, col, last_q) if len(good) else None}
        source[measure] = {}
        for dept in tri["DEPARTMENT"].unique():
            if n.get(dept, 0) >= MIN_TRIANGLE_CLAIMS:
                out[measure][dept] = reported_share(good[good["DEPARTMENT"] == dept], col, last_q)
                source[measure][dept] = "the department's own triangle"
            else:
                out[measure][dept] = out[measure]["__pooled__"]
                source[measure][dept] = "the pooled other-lines triangle"
    return out, source


def _expected_share(p: pd.DataFrame, share: dict[str, pd.Series], last_q: pd.Period) -> np.ndarray:
    """Premium-weighted share of each policy's claims expected to be recorded by the valuation quarter."""
    eff, exp = p["POLICY_EFFECTIVE_DATE"], p["POLICY_EXPIRY_DATE"]
    term = (exp - eff).dt.days.clip(lower=1).to_numpy(dtype=float)
    q0 = eff.dt.to_period("Q")
    total = np.zeros(len(p))
    for k in range(10):                                       # a cover of up to two years spans at most 9 quarters
        q = q0 + k
        start = q.dt.start_time
        end = q.dt.end_time.dt.normalize() + pd.Timedelta(days=1)
        days = ((np.minimum(exp, end) - np.maximum(eff, start)).dt.days).clip(lower=0).to_numpy(dtype=float)
        if not days.any():
            break
        s = np.ones(len(p))
        for dept, idx in p.groupby("DEPARTMENT").indices.items():
            sh = share.get(dept, share["__pooled__"])
            qq = q.iloc[idx]
            v = sh.reindex(qq.to_numpy()).to_numpy(dtype=float)
            v = np.where(qq.to_numpy() < sh.index.min(), 1.0, v)          # older than the triangle: complete
            s[idx] = np.where(np.asarray([x > last_q for x in qq]), 0.0, np.minimum(1.0, np.nan_to_num(v, nan=1.0)))
        total += days / term * s
    return total


def develop(p: pd.DataFrame, c: pd.DataFrame) -> pd.DataFrame:
    """Ultimate claims and counts per KEY for the kept policies `p` (with ZONE) and kept claims `c` (with ZONE).
    Columns: PREMIUM, RECORDED, RECORDED_TO_VAL, ULTIMATE, COUNT, ULTIMATE_COUNT, SHARE_COST, ELR, BASIS."""
    cutoff, cur = current_year()
    rec = c.groupby(KEY).agg(RECORDED=("INCURRED", "sum"), COUNT=("CLM_NO", "size"))
    out = p.groupby(KEY).agg(PREMIUM=("TOTAL_PREMIUM", "sum")).join(rec, how="outer").fillna(0.0)
    last_q = valuation_quarter(cutoff)
    shares, source = _shares(last_q) if available() else (None, None)
    if shares is None or any(shares[m]["__pooled__"] is None for m in shares):
        if shares is not None:
            _log("no class's triangle reconciles with its claims: not developed")
        out["RECORDED_TO_VAL"], out["ULTIMATE"], out["ULTIMATE_COUNT"] = out["RECORDED"], out["RECORDED"], out["COUNT"]
        out["SHARE_COST"], out["ELR"], out["BASIS"] = 1.0, np.nan, "incurred to date, not developed (no triangles)"
        return out.reset_index()
    p = p.copy()
    for m in ("COST", "COUNT"):
        p[f"SHARE_{m}"] = _expected_share(p, shares[m], last_q) * p["TOTAL_PREMIUM"]
    out = out.join(p.groupby(KEY)[["SHARE_COST", "SHARE_COUNT"]].sum())
    prem = out["PREMIUM"].replace(0, np.nan)
    out["SHARE_COST"] = (out["SHARE_COST"] / prem).fillna(1.0).clip(0, 1)
    out["SHARE_COUNT"] = (out["SHARE_COUNT"] / prem).fillna(1.0).clip(0, 1)
    acc_q = c["ACC_DATE"].dt.to_period("Q")
    to_val = acc_q.isna() | (acc_q <= last_q)
    rv = c[to_val].groupby(KEY).agg(RECORDED_TO_VAL=("INCURRED", "sum"), COUNT_TO_VAL=("CLM_NO", "size"))
    out = out.join(rv).fillna({"RECORDED_TO_VAL": 0.0, "COUNT_TO_VAL": 0.0}).reset_index()

    # Expected loss ratio (and claims per riyal) by Cape Cod, in two credibility steps so a class is not judged by
    # its department's other classes when its own history says otherwise: the class's latest CAPE_COD_YEARS years,
    # blended toward all its usable years, blended toward its department (credibility on the claim count)
    full = float(S.config["credibility"]["full_standard_claims"])
    out["USED_C"], out["USED_N"] = out["PREMIUM"] * out["SHARE_COST"], out["PREMIUM"] * out["SHARE_COUNT"]

    def rates(df: pd.DataFrame, by: list) -> pd.DataFrame:
        g = df.groupby(by).agg(R=("RECORDED_TO_VAL", "sum"), N=("COUNT_TO_VAL", "sum"), UC=("USED_C", "sum"),
                               UN=("USED_N", "sum"))
        return g.assign(LR=g["R"] / g["UC"].replace(0, np.nan), CR=g["N"] / g["UN"].replace(0, np.nan),
                        Z=np.minimum(1.0, np.sqrt(g["N"] / full)))

    def blend(own: pd.DataFrame, complement: pd.DataFrame) -> pd.DataFrame:
        b = pd.DataFrame(index=own.index)
        for r in ("LR", "CR"):
            b[r] = own["Z"] * own[r].fillna(complement[r]) + (1 - own["Z"]) * complement[r]
        return b

    cls_key = ["DEPARTMENT", "CLASS_DESC"]
    dept = rates(out, ["DEPARTMENT"])
    long = rates(out, cls_key)
    long_c = blend(long, dept.reindex(long.index.get_level_values("DEPARTMENT")).set_index(long.index))
    recent = rates(out[out["POLICY_YEAR"].between(cur - CAPE_COD_YEARS + 1, cur)], cls_key).reindex(long.index)
    recent["Z"] = recent["Z"].fillna(0.0)
    final = blend(recent, long_c)
    out = out.join(final.rename(columns={"LR": "ELR", "CR": "ECR"}), on=cls_key)
    out["ELR"], out["ECR"] = out["ELR"].fillna(0.0), out["ECR"].fillna(0.0)
    out["ULTIMATE"] = np.maximum(out["RECORDED"], out["RECORDED_TO_VAL"] + out["PREMIUM"] * out["ELR"] * (1 - out["SHARE_COST"]))
    out["ULTIMATE_COUNT"] = np.maximum(out["COUNT"], out["COUNT_TO_VAL"] + out["PREMIUM"] * out["ECR"] * (1 - out["SHARE_COUNT"]))
    out["BASIS"] = ("developed to ultimate (Bornhuetter-Ferguson, Cape Cod; cost pattern from "
                    + out["DEPARTMENT"].map(source["COST"]).fillna("the pooled other-lines triangle") + ")")
    y = out.groupby("POLICY_YEAR")[["PREMIUM", "RECORDED", "ULTIMATE"]].sum()
    _log(f"valuation {last_q}: " + ", ".join(f"{yr} {r.RECORDED / 1e6:,.1f}M -> {r.ULTIMATE / 1e6:,.1f}M"
                                             for yr, r in y.loc[cur - 3:cur].iterrows()))
    return out.drop(columns=["USED_C", "USED_N"])
