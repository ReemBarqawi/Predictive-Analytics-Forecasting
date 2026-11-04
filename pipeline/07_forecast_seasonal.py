"""
pipeline/07_forecast_seasonal.py
================================
Portfolio forecast behind the Power BI Forecast page (fact_forecast_seasonal).

Monthly earned premium, claim count and claims cost are forecast 12 months ahead with
TimesFM, Google's pretrained time-series model, from their own history. Seasonal naive
(the same month one year earlier) is the fallback and the benchmark.

Earned premium is not forecast directly: most of next year's is already written. It is the
run-off of the policies in the data (exact, from their dates) plus new business, where only
the written premium is forecast and it is earned with the policy terms of the latest year.
Claim count and cost are forecast as rates per unit of earned premium and multiplied back,
so they follow the book's known run-off instead of extending the past trend.

  1. Window from the data
     - starts one policy year after the first earned premium (the book is still being
       written before that, so premium and claims ramp up mechanically)
     - ends with the last complete month of claims data
  2. Developed history
     - recent accident months are incomplete: claims are still being reported and reserves
       still move. Each accident quarter is grossed up by its chain-ladder ultimate over what
       the warehouse triangles (sql/claims_dev_*.sql) have recorded to the data end:
       incurred for cost, reported for counts.
     - without triangles (synthetic data: ULTIMATE_CLAIM already includes IBNR) the claims
       are used as they are
  3. Rolling validation: both models forecast from every quarter-end with two years of
     history, exactly as the live forecast is made; the lower mean WAPE over premium, count
     and cost at 3 / 6 / 12 months picks the model. Its monthly errors set the 90% band, and
     the band's coverage is tested out of sample (each origin against the others' errors).
  4. Checks with pass rules: accuracy vs seasonal naive, bias, band coverage, development
     look-back (factors from a year ago vs what then happened), and input reconciliations
     (premium rebuilt from policies, triangle vs claims extract)
  5. Forecast: the chosen model, from the full history

Outputs
-------
  <facts>/fact_forecast_seasonal.csv   actual + forecast months (3M / 6M / 12M horizons)
  <facts>/fact_forecast_validation.csv every check: value, rule, PASS / REVIEW
  <reports>/seasonal_forecast_report.md
  <reports>/forecast_validation.png
"""

from __future__ import annotations

import warnings
from statistics import NormalDist

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from settings import S, year_basis, expected_claims_basis
import timesfm_model

warnings.filterwarnings("ignore")

FACTS_DIR = S.facts_dir
OUTPUT_DIR = S.reports_dir

FORECAST_HORIZONS = [3, 6, 12]          # months; one set of rows per horizon for the page slicer
# Rolling validation (method constants): forecast from every quarter-end with at least two
# years of history (two seasonal cycles) and score each horizon where the actuals exist.
MIN_CONTEXT_MONTHS = 24
VALIDATION_STEP_MONTHS = 3
# Validation pass rules (method constants, shown with every result in the report and table)
BIAS_TOLERANCE = 0.10                   # |bias| above this over all origins -> REVIEW
COVERAGE_RANGE = (0.80, 0.97)           # share of months inside the 90% band
DEV_LOOKBACK_QUARTERS = 4               # development check: factors from one year ago ...
DEV_LOOKBACK_AQS = 8                    # ... applied to the 8 accident quarters then most recent
DEV_NEWEST_TESTS = 8                    # newest-quarter check: valuations 4 ... 11 quarters ago
DEV_TOLERANCE = 0.05                    # |actual / expected development - 1| above this -> REVIEW
INPUT_TOLERANCE = 0.01                  # input reconciliations must agree within 1%
# Method constant: the book needs one policy year after the first policy is written before
# earned premium (and the claims on it) stop ramping up mechanically.
BOOK_RAMP_MONTHS = 12
# Method constant: chain-ladder factors are volume-weighted over the latest 20 quarterly
# diagonals (5 years), so the current claims process and mix drive them, not 2008 behaviour.
DEV_DIAGONALS = 20
CI_LEVEL = 0.90                         # band width written to CI_LOWER_90 / CI_UPPER_90

SERIES = ["EARNED_PREMIUM", "CLAIM_COUNT", "CLAIMS_COST"]    # shown on the page and scored
MODELLED = ["WRITTEN_PREMIUM", "CLAIM_RATE", "COST_RATE"]     # what the models forecast


def _log(msg: str) -> None:
    print(f"[forecast] {msg}", flush=True)


# =========================================================================
# Monthly history
# =========================================================================
def load_monthly() -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """Monthly premium, claim count and claims cost; plus the claims data end."""
    ep = pd.read_csv(FACTS_DIR / "fact_earned_premium.csv",
                     usecols=["MONTH_DATE", "EARNED_PREMIUM"], parse_dates=["MONTH_DATE"])
    cl = pd.read_csv(FACTS_DIR / "fact_claim.csv",
                     usecols=["CLM_NO", "ACC_DATE", "ULTIMATE_CLAIM"], parse_dates=["ACC_DATE"])
    cl = cl.dropna(subset=["ACC_DATE"])

    premium = ep.groupby(ep["MONTH_DATE"].dt.to_period("M"))["EARNED_PREMIUM"].sum()
    month = cl["ACC_DATE"].dt.to_period("M")
    claims = cl.groupby(month).agg(CLAIM_COUNT=("CLM_NO", "nunique"),
                                   CLAIMS_COST=("ULTIMATE_CLAIM", "sum"))
    monthly = premium.to_frame().join(claims, how="left").fillna(0.0)
    return monthly, premium.index.min(), cl["ACC_DATE"].max()


def history_window(first_premium: pd.Period, data_end: pd.Timestamp) -> tuple[pd.Period, pd.Period]:
    """First and last month of the modelling window: the source's forecast_window setting
    when it has one (config.toml), otherwise derived from the data."""
    configured = S.config["profiles"][S.source].get("forecast_window")
    if configured:
        _log(f"history window from config.toml [profiles.{S.source}] forecast_window")
        return pd.Period(configured[0], freq="M"), pd.Period(configured[1], freq="M")
    end = data_end.to_period("M")
    if data_end.normalize() < end.end_time.normalize():
        end -= 1                                   # the data stops part-way through this month
    return first_premium + BOOK_RAMP_MONTHS, end


# =========================================================================
# Development to ultimate
# =========================================================================
def _cumulative(tri: pd.DataFrame, value_col: str, last_q: pd.Period):
    """Cumulative triangle (accident quarter x age) as known at the end of `last_q`."""
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


def _cdf(cum: np.ndarray, age: np.ndarray) -> np.ndarray:
    """Volume-weighted chain-ladder: cumulative factor to ultimate by age (index 0 = latest)."""
    n = len(age)
    factors = np.ones(n - 1)
    for k in range(n - 1):
        rows = np.where(age >= k + 1)[0][-DEV_DIAGONALS:]
        den = cum[rows, k].sum()
        if den > 0:
            factors[k] = cum[rows, k + 1].sum() / den
    return np.append(np.cumprod(factors[::-1])[::-1], 1.0)


def development(tri: pd.DataFrame, value_col: str, last_q: pd.Period) -> tuple[np.ndarray, pd.Series]:
    """
    Chain-ladder on a triangle of increments by accident x development quarter.
    Returns the cumulative factor to ultimate by age in quarters (index 0 = the latest
    complete quarter), and per accident quarter the ratio ultimate / everything recorded so
    far. The ratio is what the claims extract needs: it was taken at the data end, part-way
    into the next quarter, so it is further developed than the last complete diagonal.
    """
    recorded = tri.groupby(pd.to_datetime(tri["ACC_QUARTER"]).dt.to_period("Q"))[value_col].sum()
    aqs, cum, age = _cumulative(tri, value_col, last_q)
    cdf = _cdf(cum, age)
    ultimate = pd.Series(cum[np.arange(len(aqs)), age] * cdf[age], index=aqs)
    ratio = (ultimate / recorded.reindex(aqs)).where(lambda r: np.isfinite(r) & (r > 0), 1.0)
    return cdf, ratio


def development_lookback(tri: pd.DataFrame, value_col: str, last_q: pd.Period) -> pd.DataFrame:
    """
    Actual vs expected development: factors estimated as at DEV_LOOKBACK_QUARTERS ago predict
    how much each accident quarter would grow since then; compare with what it actually did.
    """
    then = last_q - DEV_LOOKBACK_QUARTERS
    aqs_then, cum_then, age_then = _cumulative(tri, value_col, then)
    aqs_now, cum_now, age_now = _cumulative(tri, value_col, last_q)
    cdf_then = _cdf(cum_then, age_then)
    rows = []
    for i, a in enumerate(aqs_then[-DEV_LOOKBACK_AQS:], start=len(aqs_then) - DEV_LOOKBACK_AQS):
        known = cum_then[i, age_then[i]]
        j = aqs_now.get_loc(a)
        grow = cdf_then[age_then[i]] / cdf_then[min(age_then[i] + DEV_LOOKBACK_QUARTERS, len(cdf_then) - 1)]
        rows.append({"ACC_QUARTER": str(a), "KNOWN_THEN": known, "EXPECTED_NOW": known * grow,
                     "ACTUAL_NOW": cum_now[j, age_now[j]]})
    out = pd.DataFrame(rows)
    out["ACTUAL_VS_EXPECTED"] = out["ACTUAL_NOW"] / out["EXPECTED_NOW"]
    return out


def development_newest(tri: pd.DataFrame, value_col: str, last_q: pd.Period) -> pd.DataFrame:
    """
    The newest accident quarter carries the largest factor and drives the forecast, so it is
    tested on its own: at each of DEV_NEWEST_TESTS past valuations, the factors then predict
    how far that quarter would grow by now; compare with what it actually reached.
    """
    aqs_now, cum_now, age_now = _cumulative(tri, value_col, last_q)
    rows = []
    for lag in range(DEV_LOOKBACK_QUARTERS, DEV_LOOKBACK_QUARTERS + DEV_NEWEST_TESTS):
        aqs, cum, age = _cumulative(tri, value_col, last_q - lag)
        cdf = _cdf(cum, age)
        j = aqs_now.get_loc(aqs[-1])
        expected = cum[-1, 0] * cdf[0] / cdf[min(lag, len(cdf) - 1)]
        rows.append({"VALUED_AT": str(last_q - lag), "ACTUAL_VS_EXPECTED": cum_now[j, age_now[j]] / expected})
    return pd.DataFrame(rows)


def develop(monthly: pd.DataFrame, data_end: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Gross claim count and cost up to ultimate. Returns the frame and a log of what was done."""
    out = monthly.copy()
    out["REPORTED_COST"] = out["CLAIMS_COST"]
    out["REPORTED_COUNT"] = out["CLAIM_COUNT"]

    files = {"CLAIMS_COST": ("claims_dev_incurred", "INCURRED_CHANGE"),
             "CLAIM_COUNT": ("claims_dev_reported", "CLAIMS_REPORTED")}
    paths = {k: S.raw_dir / f"{name}.parquet" for k, (name, _) in files.items()} if S.raw_dir else {}
    if not paths or not all(p.exists() for p in paths.values()):
        if S.source == "oracle":
            _log("WARNING: claim triangles missing from the raw folder — recent months are NOT "
                 "developed (run 00_extract_oracle.py to pull sql/claims_dev_*.sql)")
        else:
            _log("no claim triangles for this source — ULTIMATE_CLAIM used as already developed")
        return out, {"developed": False}

    q_end = data_end.to_period("Q")
    last_q = q_end if data_end.normalize() >= q_end.end_time.normalize() else q_end - 1
    info = {"developed": True, "valuation_quarter": str(last_q), "cdf": {}, "latest_ratio": {},
            "lookback": {}, "newest": {}, "tri_vs_extract": {}}
    for col, (name, value_col) in files.items():
        tri = pd.read_parquet(paths[col])
        cdf, ratio = development(tri, value_col, last_q)
        # Accident quarters outside the triangle (before its history, or the partial quarter
        # after last_q, which the forecast window never uses) stay as recorded.
        out[col] = out[col] * ratio.reindex(out.index.asfreq("Q")).fillna(1.0).to_numpy()
        info["cdf"][col] = cdf[:8]
        info["latest_ratio"][col] = float(ratio.iloc[-1])
        info["lookback"][col] = development_lookback(tri, value_col, last_q)
        info["newest"][col] = development_newest(tri, value_col, last_q)
        # Same claims, two extracts: the triangle and fact_claim must agree on recent quarters
        quarters = pd.period_range(last_q - 7, last_q, freq="Q")
        in_tri = tri.groupby(pd.to_datetime(tri["ACC_QUARTER"]).dt.to_period("Q"))[value_col].sum()
        reported = "REPORTED_COST" if col == "CLAIMS_COST" else "REPORTED_COUNT"
        in_extract = out[reported].groupby(out.index.asfreq("Q")).sum()
        info["tri_vs_extract"][col] = float(in_tri.reindex(quarters).sum() / in_extract.reindex(quarters).sum())
        _log(f"{name}: factor to ultimate {cdf[0]:.3f} at quarter-end; the latest quarter as "
             f"extracted x{ratio.iloc[-1]:.3f}, one year back x{ratio.iloc[-5]:.3f}")
    return out, info


# =========================================================================
# Premium: run-off of written policies + new business
# =========================================================================
def premium_cohorts() -> pd.DataFrame:
    """
    Earned premium by written month (rows) x earned month (columns): each policy's premium
    split over the months it is in force, pro rata to days, as in fact_earned_premium.
    """
    p = pd.read_csv(FACTS_DIR / "fact_policy.csv",
                    usecols=["CLASS_DESC", "POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE", "TOTAL_PREMIUM"],
                    parse_dates=["POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE"], low_memory=False)
    # Same classes as fact_earned_premium (its builder may use another class list than this run)
    classes = pd.read_csv(FACTS_DIR / "fact_earned_premium.csv", usecols=["CLASS_DESC"])["CLASS_DESC"].unique()
    p = p[p["CLASS_DESC"].isin(classes) & (p["POLICY_EXPIRY_DATE"] > p["POLICY_EFFECTIVE_DATE"])]
    # Policies sharing both dates earn identically: total them first (millions -> thousands)
    g = p.groupby(["POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE"])["TOTAL_PREMIUM"].sum().reset_index()

    eff = g["POLICY_EFFECTIVE_DATE"].to_numpy("datetime64[D]")
    exp = g["POLICY_EXPIRY_DATE"].to_numpy("datetime64[D]")
    per_day = g["TOTAL_PREMIUM"].to_numpy(dtype=float) / (exp - eff).astype(np.int64)
    m0 = eff.astype("datetime64[M]")
    n_months = (exp.astype("datetime64[M]") - m0).astype(np.int64) + 1
    row = np.repeat(np.arange(len(g)), n_months)
    month = m0[row] + (np.arange(len(row)) - np.repeat(np.cumsum(n_months) - n_months, n_months))
    days = (np.minimum((month + 1).astype("datetime64[D]"), exp[row])
            - np.maximum(month.astype("datetime64[D]"), eff[row])).astype(np.int64)
    keep = days > 0
    pm = pd.DataFrame({"W": pd.PeriodIndex(m0[row][keep], freq="M"),
                       "E": pd.PeriodIndex(month[keep], freq="M"),
                       "EP": per_day[row][keep] * days[keep]})
    return pm.groupby(["W", "E"])["EP"].sum().unstack(fill_value=0.0)


def earned_premium(cohorts: pd.DataFrame, origin: pd.Period,
                   written: pd.Series) -> tuple[pd.Series, pd.Series]:
    """
    Earned premium in the months of `written` (new written premium per month after `origin`):
    the run-off of everything written up to `origin`, plus the new business earned with the
    earning pattern of the policies written in the 12 months to `origin`.
    Returns (earned, run-off part).
    """
    months = written.index
    runoff = cohorts.loc[:origin].sum().reindex(months, fill_value=0.0)

    recent = cohorts.loc[origin - 11:origin].stack()
    offset = [(e - w).n for w, e in recent.index]
    pattern = recent.groupby(offset).sum() / recent.sum()       # share earned k months after writing

    new = pd.Series(0.0, index=months)
    for w, amount in written.items():
        for e in months[months >= w]:
            new[e] += amount * pattern.get((e - w).n, 0.0)
    return runoff + new, runoff


# =========================================================================
# Models
# =========================================================================
def seasonal_naive(history: pd.Series, horizon: int) -> np.ndarray:
    return history.iloc[-12:].to_numpy()[np.arange(horizon) % 12]


def forecast(model: str, history: pd.DataFrame, cohorts: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """
    Written premium and claims per unit of earned premium are forecast; earned premium comes
    from the run-off plus new business, and claims = rate x earned premium, so the claims
    follow the book's known growth or shrinkage instead of extending the past trend.
    """
    h = history.assign(CLAIM_RATE=history["CLAIM_COUNT"] / history["EARNED_PREMIUM"],
                       COST_RATE=history["CLAIMS_COST"] / history["EARNED_PREMIUM"])
    if model == "timesfm":
        preds = timesfm_model.predict([h[s].to_numpy() for s in MODELLED], horizon)
    else:
        preds = [seasonal_naive(h[s], horizon) for s in MODELLED]
    index = pd.period_range(history.index[-1] + 1, periods=horizon, freq="M")
    fc = pd.DataFrame({s: np.clip(p, 0, None) for s, p in zip(MODELLED, preds)}, index=index)
    fc["EARNED_PREMIUM"], fc["RUNOFF_PREMIUM"] = earned_premium(cohorts, history.index[-1],
                                                                fc["WRITTEN_PREMIUM"])
    fc["CLAIM_COUNT"] = fc["CLAIM_RATE"] * fc["EARNED_PREMIUM"]
    fc["CLAIMS_COST"] = fc["COST_RATE"] * fc["EARNED_PREMIUM"]
    return fc


# =========================================================================
# Rolling validation
# =========================================================================
def rolling_backtest(history: pd.DataFrame, cohorts: pd.DataFrame, models: list[str]) -> pd.DataFrame:
    """
    Forecast from every quarter-end origin with MIN_CONTEXT_MONTHS of history before it and at
    least the shortest horizon of actuals after it, exactly as the live forecast is made.
    One row per origin x model x month ahead, with forecast and actual for each series.
    """
    last = history.index[-1]
    origins = [o for o in history.index[MIN_CONTEXT_MONTHS - 1:]
               if o.month % VALIDATION_STEP_MONTHS == 0 and o + min(FORECAST_HORIZONS) <= last]
    rows = []
    for o in origins:
        actual = history.loc[o + 1:o + max(FORECAST_HORIZONS)]
        for m in models:
            pred = forecast(m, history.loc[:o], cohorts, max(FORECAST_HORIZONS)).loc[actual.index]
            for step, month in enumerate(actual.index, start=1):
                rows.append({"ORIGIN": o, "MODEL": m, "STEP": step, "MONTH": month,
                             **{f"PRED_{s}": pred.at[month, s] for s in SERIES},
                             **{f"ACT_{s}": actual.at[month, s] for s in SERIES}})
    return pd.DataFrame(rows)


def score(bt: pd.DataFrame) -> pd.DataFrame:
    """WAPE and bias of the h-month totals, per model x series x horizon, over all origins."""
    rows = []
    for h in FORECAST_HORIZONS:
        full = bt[bt["STEP"] <= h].groupby(["MODEL", "ORIGIN"]).filter(lambda g: len(g) == h)
        tot = full.groupby(["MODEL", "ORIGIN"]).sum(numeric_only=True)
        for m, g in tot.groupby(level="MODEL"):
            for s in SERIES:
                err = g[f"PRED_{s}"] - g[f"ACT_{s}"]
                rows.append({"MODEL": m, "SERIES": s, "HORIZON": h, "N_ORIGINS": len(g),
                             "WAPE": err.abs().sum() / g[f"ACT_{s}"].sum(),
                             "BIAS": err.sum() / g[f"ACT_{s}"].sum()})
    return pd.DataFrame(rows)


def band(bt: pd.DataFrame, model: str) -> tuple[float, float]:
    """
    Relative spread of the model's monthly claims-cost errors (the live forecast band), and
    its out-of-sample coverage: each origin's months are checked against a band built from
    the other origins only.
    """
    b = bt[bt["MODEL"] == model]
    rel = (b["PRED_CLAIMS_COST"] - b["ACT_CLAIMS_COST"]) / b["ACT_CLAIMS_COST"]
    z = NormalDist().inv_cdf(0.5 + CI_LEVEL / 2)
    inside = []
    for o in b["ORIGIN"].unique():
        own = (b["ORIGIN"] == o).to_numpy()
        sd = rel[~own].std(ddof=1)
        inside.extend((rel[own].abs() <= z * sd).tolist())
    return float(rel.std(ddof=1)), float(np.mean(inside))


def seasonal_index(claims: pd.Series) -> pd.Series:
    """Claims in each calendar month relative to the centred 12-month average."""
    trend = claims.rolling(12, center=True).mean()
    ratio = (claims / trend).dropna()
    return ratio.groupby(ratio.index.month).mean()


# =========================================================================
# Output
# =========================================================================
def written_month_view(history: pd.DataFrame, fc: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    [report] year_basis = "written_year": each policy's whole premium and claims sit in the month it
    starts, so a year's 12 months add up to the policy-year figures on Profitability. Claims follow
    step 05: actual for mature years, model-expected on priced policies for the others. A policy whose
    start falls outside its POLICY_YEAR goes to that year's first or last month. The models still run
    on the whole book; this only changes what the page shows. Forecast: written premium at the
    forecast claim rates.
    """
    pp = pd.read_csv(FACTS_DIR / "fact_policy_pricing.csv",
                     usecols=["POLICY_NO", "POLICY_YEAR", "POLICY_EFFECTIVE_DATE", "TOTAL_PREMIUM", "charged_premium",
                              "EXPOSURE_YEARS", "pure_premium_period", "expected_frequency_period"],
                     parse_dates=["POLICY_EFFECTIVE_DATE"], low_memory=False)
    eff = pp["POLICY_EFFECTIVE_DATE"]
    number = np.where(eff.dt.year == pp["POLICY_YEAR"], eff.dt.month, np.where(eff.dt.year < pp["POLICY_YEAR"], 1, 12))
    pp["M"] = pd.PeriodIndex(year=pp["POLICY_YEAR"], month=number, freq="M")
    priced = (pp["charged_premium"] > 0) & (pp["EXPOSURE_YEARS"] > 0)

    cl = pd.read_csv(FACTS_DIR / "fact_claim.csv", usecols=["POLICY_NO", "POLICY_YEAR", "CLM_NO", "POLICY_MATCHED", "ULTIMATE_CLAIM"])
    cl = cl[cl["POLICY_MATCHED"].astype(str).str.lower() == "true"]
    cl = cl.merge(pp[["POLICY_NO", "POLICY_YEAR", "M"]].drop_duplicates(["POLICY_NO", "POLICY_YEAR"]), on=["POLICY_NO", "POLICY_YEAR"])

    basis = pd.read_csv(FACTS_DIR / "fact_segment_performance_by_year.csv",
                        usecols=["POLICY_YEAR", "CLAIMS_BASIS", "ULTIMATE_CLAIMS", "CLAIM_COUNT"])
    mature = set(basis.loc[basis["CLAIMS_BASIS"].str.startswith("Actual"), "POLICY_YEAR"])
    year_totals = basis.groupby("POLICY_YEAR")[["ULTIMATE_CLAIMS", "CLAIM_COUNT"]].sum()
    is_mature = pd.Series(history.index.year.isin(mature), index=history.index)

    h = history.copy()
    h["EARNED_PREMIUM"] = pp.groupby("M")["TOTAL_PREMIUM"].sum().reindex(h.index, fill_value=0.0)
    h["CLAIMS_COST"] = cl.groupby("M")["ULTIMATE_CLAIM"].sum().reindex(h.index, fill_value=0.0).where(
        is_mature, pp[priced].groupby("M")["pure_premium_period"].sum().reindex(h.index, fill_value=0.0))
    h["CLAIM_COUNT"] = cl.groupby("M")["CLM_NO"].size().reindex(h.index, fill_value=0).astype(float).where(
        is_mature, pp[priced].groupby("M")["expected_frequency_period"].sum().reindex(h.index, fill_value=0.0))

    # Years on an estimate: spread that year's total over its months by the model's monthly shape
    for col, total in (("CLAIMS_COST", "ULTIMATE_CLAIMS"), ("CLAIM_COUNT", "CLAIM_COUNT")):
        for year, g in h[~is_mature].groupby(h[~is_mature].index.year):
            if year in year_totals.index and g[col].sum() > 0:
                h.loc[g.index, col] = g[col] * year_totals.at[year, total] / g[col].sum()

    f = fc.copy()
    f["EARNED_PREMIUM"] = fc["WRITTEN_PREMIUM"]
    f["CLAIM_COUNT"] = fc["CLAIM_RATE"] * f["EARNED_PREMIUM"]
    f["CLAIMS_COST"] = fc["COST_RATE"] * f["EARNED_PREMIUM"]
    return h, f


def trend_factor(history: pd.DataFrame, fc: pd.DataFrame, bt: pd.DataFrame, model: str) -> dict:
    """
    Expected change in claims per riyal of premium, next 12 months vs the last 12: the forecast's, less
    its average error on past years. The raw forecast ran high on past origins, so it does not drive
    prices directly. Also records how it compared with assuming no change.
    """
    rows = []
    for origin, g in bt[bt["MODEL"] == model].groupby("ORIGIN"):
        if len(g) < 12:
            continue
        prior = history.loc[origin - 11:origin]
        rows.append({"BEFORE": prior["CLAIMS_COST"].sum() / prior["EARNED_PREMIUM"].sum(),
                     "PRED": g["PRED_CLAIMS_COST"].sum() / g["PRED_EARNED_PREMIUM"].sum(),
                     "ACT": g["ACT_CLAIMS_COST"].sum() / g["ACT_EARNED_PREMIUM"].sum()})
    t = pd.DataFrame(rows)
    bias = (t["PRED"] - t["ACT"]).mean()
    last12 = history.iloc[-12:]
    before = last12["CLAIMS_COST"].sum() / last12["EARNED_PREMIUM"].sum()
    raw = fc.head(12)["CLAIMS_COST"].sum() / fc.head(12)["EARNED_PREMIUM"].sum()
    return {"TREND_FACTOR": (raw - bias) / before, "TREND_FACTOR_RAW": raw / before, "BIAS_PTS": bias * 100,
            "N_ORIGINS": len(t), "ERROR_FORECAST_PTS": (t["PRED"] - t["ACT"]).abs().mean() * 100,
            "ERROR_CORRECTED_PTS": (t["PRED"] - bias - t["ACT"]).abs().mean() * 100,
            "ERROR_NO_CHANGE_PTS": (t["BEFORE"] - t["ACT"]).abs().mean() * 100,
            "DIRECTION_RIGHT": int((np.sign(t["PRED"] - t["BEFORE"]) == np.sign(t["ACT"] - t["BEFORE"])).sum())}


def build_rows(history: pd.DataFrame, fc: pd.DataFrame, model: str, model_wape: float,
               rel_sd: float, season: pd.Series) -> pd.DataFrame:
    z = NormalDist().inv_cdf(0.5 + CI_LEVEL / 2)
    rows = []
    for m, r in history.iterrows():
        rows.append({
            "MONTH_DATE": m.to_timestamp(), "FORECAST_HORIZON": "ACTUAL", "PERIOD_TYPE": "actual",
            "FORECAST_CLAIMS": r["CLAIMS_COST"], "FORECAST_PREMIUM": r["EARNED_PREMIUM"],
            "CI_LOWER_90": np.nan, "CI_UPPER_90": np.nan, "FORECAST_CLAIM_COUNT": r["CLAIM_COUNT"],
            "SEASONAL_INDEX": 1.0, "MODEL_TYPE": "actual", "MODEL_WAPE": np.nan, "BIAS_CORRECTION": np.nan,
        })
    for h in FORECAST_HORIZONS:
        for m, r in fc.head(h).iterrows():
            prem = r["EARNED_PREMIUM"]
            rows.append({
                "MONTH_DATE": m.to_timestamp(), "FORECAST_HORIZON": f"{h}M", "PERIOD_TYPE": "forecast",
                "FORECAST_CLAIMS": r["CLAIMS_COST"], "FORECAST_PREMIUM": prem,
                # Band on claims from the rolling-validation errors, as a loss ratio on point premium
                "CI_LOWER_90": max(0.0, r["CLAIMS_COST"] * (1 - z * rel_sd)) / prem if prem > 0 else np.nan,
                "CI_UPPER_90": r["CLAIMS_COST"] * (1 + z * rel_sd) / prem if prem > 0 else np.nan,
                "FORECAST_CLAIM_COUNT": r["CLAIM_COUNT"],
                "SEASONAL_INDEX": season.get(m.month, 1.0), "MODEL_TYPE": model,
                "MODEL_WAPE": model_wape, "BIAS_CORRECTION": 1.0,
            })
    out = pd.DataFrame(rows)
    out.insert(0, "SEGMENT_ID", "PORTFOLIO")
    out["FORECAST_LOSS_RATIO"] = np.where(out["FORECAST_PREMIUM"] > 0,
                                          out["FORECAST_CLAIMS"] / out["FORECAST_PREMIUM"], np.nan)
    out["FORECAST_AVG_SEVERITY"] = np.where(out["FORECAST_CLAIM_COUNT"] > 0,
                                            out["FORECAST_CLAIMS"] / out["FORECAST_CLAIM_COUNT"], np.nan)
    out["MONTH_DATE"] = out["MONTH_DATE"].dt.strftime("%Y-%m-%d")
    cols = ["SEGMENT_ID", "MONTH_DATE", "FORECAST_HORIZON", "PERIOD_TYPE", "FORECAST_CLAIMS",
            "FORECAST_PREMIUM", "FORECAST_LOSS_RATIO", "CI_LOWER_90", "CI_UPPER_90",
            "FORECAST_CLAIM_COUNT", "FORECAST_AVG_SEVERITY", "SEASONAL_INDEX", "MODEL_TYPE",
            "MODEL_WAPE", "BIAS_CORRECTION"]
    return out[cols]


LABEL = {"timesfm": "TimesFM", "seasonal_naive": "Seasonal naive"}
NAMES = {"EARNED_PREMIUM": "Earned premium", "CLAIM_COUNT": "Claim count", "CLAIMS_COST": "Claims cost"}


def validation_table(scores: pd.DataFrame, model: str, coverage: float, dev: dict,
                     premium_check: float, data_end: pd.Timestamp) -> pd.DataFrame:
    """Every check with its value, pass rule and status: one table for the report and Power BI."""
    rows = []

    def add(group, check, measure, value, threshold, ok, horizon="", benchmark=np.nan):
        rows.append({"CHECK_GROUP": group, "CHECK": check, "MEASURE": measure, "HORIZON": horizon,
                     "MODEL": LABEL[model], "VALUE": value, "BENCHMARK": benchmark,
                     "THRESHOLD": threshold, "STATUS": "INFO" if ok is None else ("PASS" if ok else "REVIEW")})

    for r in scores[scores["MODEL"] == model].itertuples():
        naive = scores[(scores["MODEL"] == "seasonal_naive") & (scores["SERIES"] == r.SERIES)
                       & (scores["HORIZON"] == r.HORIZON)]["WAPE"].iloc[0]
        vs_naive = None if model == "seasonal_naive" else r.WAPE <= naive
        add("accuracy", f"Error (WAPE) over {r.N_ORIGINS} forecast origins, vs seasonal naive",
            NAMES[r.SERIES], r.WAPE, "at or below seasonal naive", vs_naive, f"{r.HORIZON}M", naive)
        add("accuracy", f"Bias over {r.N_ORIGINS} forecast origins", NAMES[r.SERIES], r.BIAS,
            f"within ±{BIAS_TOLERANCE:.0%}", abs(r.BIAS) <= BIAS_TOLERANCE, f"{r.HORIZON}M")
    add("band", f"Months inside the {CI_LEVEL:.0%} band (each origin tested on a band from the others)",
        "Claims cost", coverage, f"{COVERAGE_RANGE[0]:.0%} to {COVERAGE_RANGE[1]:.0%}",
        COVERAGE_RANGE[0] <= coverage <= COVERAGE_RANGE[1])
    add("inputs", "Earned premium rebuilt from policy dates / fact_earned_premium", "Earned premium",
        premium_check, f"within ±{INPUT_TOLERANCE:.0%}", abs(premium_check - 1) <= INPUT_TOLERANCE)
    if dev["developed"]:
        for col, lb in dev["lookback"].items():
            v = lb["ACTUAL_NOW"].sum() / lb["EXPECTED_NOW"].sum()
            add("development", f"Actual / expected development over the last {DEV_LOOKBACK_QUARTERS} "
                f"quarters ({DEV_LOOKBACK_AQS} accident quarters, factors as at then)", NAMES[col], v,
                f"within ±{DEV_TOLERANCE:.0%}", abs(v - 1) <= DEV_TOLERANCE)
        for col, nq in dev["newest"].items():
            v = nq["ACTUAL_VS_EXPECTED"].mean()
            add("development", f"Newest accident quarter: actual / expected development, mean of "
                f"{len(nq)} past valuations ({nq['VALUED_AT'].iloc[-1]} to {nq['VALUED_AT'].iloc[0]})",
                NAMES[col], v, f"within ±{DEV_TOLERANCE:.0%}", abs(v - 1) <= DEV_TOLERANCE)
        for col, v in dev["tri_vs_extract"].items():
            add("inputs", "Claim triangle / claims extract, last 8 accident quarters", NAMES[col], v,
                f"within ±{INPUT_TOLERANCE:.0%}", abs(v - 1) <= INPUT_TOLERANCE)
    out = pd.DataFrame(rows)
    out["DATA_TO"] = data_end.date().isoformat()
    return out


def write_chart(history: pd.DataFrame, fc: pd.DataFrame, rel_sd: float) -> None:
    z = NormalDist().inv_cdf(0.5 + CI_LEVEL / 2)
    x_h, x_f = history.index.to_timestamp(), fc.index.to_timestamp()
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(x_h, history["REPORTED_COST"] / 1e6, color="grey", lw=1, label="Reported so far")
    ax.plot(x_h, history["CLAIMS_COST"] / 1e6, color="black", lw=1.5, label="Developed to ultimate")
    ax.plot(x_f, fc["CLAIMS_COST"] / 1e6, color="tab:red", lw=1.5, label="Forecast")
    ax.fill_between(x_f, fc["CLAIMS_COST"] * (1 - z * rel_sd) / 1e6,
                    fc["CLAIMS_COST"] * (1 + z * rel_sd) / 1e6, color="tab:red", alpha=0.15,
                    label=f"{CI_LEVEL:.0%} band")
    ax.set_ylabel("Claims cost per month (millions)")
    ax.grid(alpha=0.2)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "forecast_validation.png", dpi=120)
    plt.close(fig)


def write_report(history: pd.DataFrame, fc: pd.DataFrame, scores: pd.DataFrame, model: str,
                 checks: pd.DataFrame, dev: dict, data_end: pd.Timestamp) -> None:
    label, names = LABEL, NAMES
    last12 = history.iloc[-12:]
    with open(OUTPUT_DIR / "seasonal_forecast_report.md", "w", encoding="utf-8") as f:
        f.write("# Portfolio Forecast Report\n\n")
        f.write(f"Data source: **{S.source}**. Claims data to {data_end.date()}.\n\n")
        f.write("## Window\n\n")
        why = ("Set by forecast_window in config.toml." if S.config["profiles"][S.source].get("forecast_window")
               else f"Starts {BOOK_RAMP_MONTHS} months after the first earned premium; ends with the "
                    "last complete month of claims data.")
        f.write(f"- History: {history.index[0]} to {history.index[-1]} ({len(history)} months). {why}\n")
        f.write(f"- Forecast: {fc.index[0]} to {fc.index[-1]}.\n\n")

        known = fc["RUNOFF_PREMIUM"].sum() / fc["EARNED_PREMIUM"].sum()
        f.write(f"Earned premium = run-off of policies already written ({known:.0%} of the forecast "
                "year, exact) + new business (forecast written premium, earned with the latest "
                "year's policy terms).\n\n")
        f.write("## Development to ultimate\n\n")
        if dev["developed"]:
            f.write(f"Chain-ladder at {dev['valuation_quarter']}, volume-weighted over the latest "
                    f"{DEV_DIAGONALS} diagonals. Factor to ultimate by age of the accident quarter:\n\n")
            f.write("| Age (quarters) | " + " | ".join(str(i) for i in range(8)) + " |\n")
            f.write("|---|" + "---|" * 8 + "\n")
            for col, cdf in dev["cdf"].items():
                f.write(f"| {names[col]} | " + " | ".join(f"{v:.3f}" for v in cdf) + " |\n")
            f.write("\nThe claims extract runs past the last quarter-end, so each accident quarter is "
                    "scaled by its ultimate over everything recorded to the data end "
                    f"(latest quarter: cost x{dev['latest_ratio']['CLAIMS_COST']:.3f}, "
                    f"count x{dev['latest_ratio']['CLAIM_COUNT']:.3f}).\n")
            up = last12["CLAIMS_COST"].sum() / last12["REPORTED_COST"].sum() - 1
            f.write(f"\nThe last 12 months of claims cost are {up:+.1%} above what is reported so far.\n\n")
        else:
            f.write("Not applied: no claim triangles for this source (claims used as recorded).\n\n")

        n_origins = int(scores["N_ORIGINS"].max())
        f.write(f"## Validation ({n_origins} forecast origins, every quarter-end with "
                f"{MIN_CONTEXT_MONTHS}+ months of history)\n\n")
        n_review = int((checks["STATUS"] == "REVIEW").sum())
        f.write(f"**{(checks['STATUS'] == 'PASS').sum()} checks pass, {n_review} need review.** "
                "The same rows are in fact_forecast_validation.csv for Power BI.\n\n")
        f.write("| Group | Check | Measure | Horizon | Value | Benchmark | Rule | Status |\n")
        f.write("|---|---|---|---|---:|---:|---|---|\n")
        for r in checks.itertuples():
            pct = r.CHECK_GROUP in ("accuracy", "band")
            val = f"{r.VALUE:+.1%}" if r.CHECK.startswith("Bias") else (f"{r.VALUE:.1%}" if pct else f"{r.VALUE:.3f}")
            bench = f"{r.BENCHMARK:.1%}" if pd.notna(r.BENCHMARK) else ""
            f.write(f"| {r.CHECK_GROUP} | {r.CHECK} | {r.MEASURE} | {r.HORIZON} | {val} | {bench} | "
                    f"{r.THRESHOLD} | **{r.STATUS}** |\n")

        f.write("\n### Model choice\n\n| Model | Horizon | " +
                " | ".join(f"{names[s]} WAPE" for s in SERIES) + " | Claims cost bias |\n")
        f.write("|---|---|" + "---|" * (len(SERIES) + 1) + "\n")
        for (m, h), g in scores.groupby(["MODEL", "HORIZON"]):
            g = g.set_index("SERIES")
            f.write(f"| {label[m]} | {h}M | " + " | ".join(f"{g.at[s, 'WAPE']:.1%}" for s in SERIES) +
                    f" | {g.at['CLAIMS_COST', 'BIAS']:+.1%} |\n")
        f.write(f"\n**Selected: {label[model]}** (lowest mean WAPE over the three measures and horizons).")
        if "timesfm" not in set(scores["MODEL"]):
            f.write(" TimesFM is not installed, so only seasonal naive was available.")
        f.write("\nActuals for the latest months are themselves developed estimates, and the "
                "development factors are today's: the test shows how well the method forecasts the "
                "developed book, not what the dashboard would have shown at each origin.\n\n")

        if dev["developed"]:
            f.write(f"### Development look-back (factors as at {DEV_LOOKBACK_QUARTERS} quarters ago)\n\n")
            for col, lb in dev["lookback"].items():
                f.write(f"**{names[col]}**\n\n| Accident quarter | Known then | Expected now | "
                        "Actual now | Actual / expected |\n|---|---:|---:|---:|---:|\n")
                for r in lb.itertuples():
                    f.write(f"| {r.ACC_QUARTER} | {r.KNOWN_THEN:,.0f} | {r.EXPECTED_NOW:,.0f} | "
                            f"{r.ACTUAL_NOW:,.0f} | {r.ACTUAL_VS_EXPECTED:.3f} |\n")
                f.write("\n")
            f.write("### Newest accident quarter at past valuations (actual / expected development)\n\n")
            f.write("| Valued at | " + " | ".join(names[c] for c in dev["newest"]) + " |\n")
            f.write("|---|" + "---:|" * len(dev["newest"]) + "\n")
            tests = pd.concat({c: d.set_index("VALUED_AT")["ACTUAL_VS_EXPECTED"] for c, d in dev["newest"].items()}, axis=1)
            for q, r in tests.iterrows():
                f.write(f"| {q} | " + " | ".join(f"{v:.3f}" for v in r) + " |\n")
            f.write("\nBelow 1 means the factors over-stated how much the newest quarter would grow. "
                    "A consistent gap here moves the latest months of developed history, and through "
                    "them the forecast loss ratio.\n\n")

        f.write("## Next 12 months vs the last 12\n\n")
        f.write("| | Last 12 months | Forecast | Change |\n|---|---:|---:|---:|\n")
        for s in SERIES:
            a, b = last12[s].sum(), fc[s].sum()
            f.write(f"| {names[s]} | {a:,.0f} | {b:,.0f} | {b / a - 1:+.1%} |\n")
        lr_a = last12["CLAIMS_COST"].sum() / last12["EARNED_PREMIUM"].sum()
        lr_b = fc["CLAIMS_COST"].sum() / fc["EARNED_PREMIUM"].sum()
        f.write(f"| Loss ratio | {lr_a:.1%} | {lr_b:.1%} | {100 * (lr_b - lr_a):+.1f} pts |\n\n")

        f.write("## Monthly forecast\n\n| Month | Premium | Claims | Claim count | Loss ratio |\n")
        f.write("|---|---:|---:|---:|---:|\n")
        for m, r in fc.iterrows():
            f.write(f"| {m} | {r['EARNED_PREMIUM']:,.0f} | {r['CLAIMS_COST']:,.0f} | "
                    f"{r['CLAIM_COUNT']:,.0f} | {r['CLAIMS_COST'] / r['EARNED_PREMIUM']:.1%} |\n")


# =========================================================================
# Entry point
# =========================================================================
def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    monthly, first_premium, data_end = load_monthly()
    start, end = history_window(first_premium, data_end)
    _log(f"claims data to {data_end.date()}; history {start} to {end}")

    monthly, dev = develop(monthly, data_end)
    cohorts = premium_cohorts()
    monthly["WRITTEN_PREMIUM"] = cohorts.sum(axis=1).reindex(monthly.index, fill_value=0.0)
    history = monthly.loc[start:end]

    models = (["timesfm"] if timesfm_model.available() else []) + ["seasonal_naive"]
    if "timesfm" not in models:
        _log("TimesFM not installed — seasonal naive only")
    bt = rolling_backtest(history, cohorts, models)
    scores = score(bt)
    mean_wape = scores.groupby("MODEL")["WAPE"].mean()
    for m, v in mean_wape.items():
        _log(f"validation {m}: mean WAPE {v:.1%} over {bt[bt['MODEL'] == m]['ORIGIN'].nunique()} origins")
    model = mean_wape.idxmin()
    rel_sd, coverage = band(bt, model)
    b = bt[bt["MODEL"] == model]
    monthly_wape = float((b["PRED_CLAIMS_COST"] - b["ACT_CLAIMS_COST"]).abs().sum() / b["ACT_CLAIMS_COST"].sum())
    _log(f"selected: {model}; band coverage {coverage:.0%} out of sample")

    # Input check: the premium rebuilt from policy dates must equal fact_earned_premium
    premium_check = float(cohorts.sum().reindex(history.index, fill_value=0.0).sum()
                          / history["EARNED_PREMIUM"].sum())
    checks = validation_table(scores, model, coverage, dev, premium_check, data_end)
    checks.to_csv(FACTS_DIR / "fact_forecast_validation.csv", index=False, encoding="utf-8")
    review = checks[checks["STATUS"] == "REVIEW"]
    _log(f"checks: {(checks['STATUS'] == 'PASS').sum()} pass, {len(review)} need review"
         + "".join(f"\n           REVIEW {r.CHECK_GROUP}: {r.MEASURE} {r.HORIZON} = {r.VALUE:.3f} ({r.THRESHOLD})"
                   for r in review.itertuples()))

    fc = forecast(model, history, cohorts, max(FORECAST_HORIZONS))
    trend = trend_factor(history, fc, bt, model)
    pd.DataFrame([trend]).to_csv(FACTS_DIR / "fact_trend_factor.csv", index=False, encoding="utf-8")
    _log(f"trend factor {trend['TREND_FACTOR']:.3f} (forecast {trend['TREND_FACTOR_RAW']:.3f}, past bias "
         f"{trend['BIAS_PTS']:+.1f} pts; error {trend['ERROR_CORRECTED_PTS']:.1f} vs no change "
         f"{trend['ERROR_NO_CHANGE_PTS']:.1f} pts over {trend['N_ORIGINS']} origins)")
    shown_history, shown_fc = written_month_view(history, fc) if year_basis() == "written_year" else (history, fc)
    if year_basis() == "written_year" and expected_claims_basis() == "experience":
        # The page's forecast year is the current year's loss ratio times the trend factor; months keep their shape.
        recent = shown_history.iloc[-12:]
        wanted = trend["TREND_FACTOR"] * recent["CLAIMS_COST"].sum() / recent["EARNED_PREMIUM"].sum()
        head = shown_fc.head(12)
        scale = wanted / (head["CLAIMS_COST"].sum() / head["EARNED_PREMIUM"].sum())
        shown_fc = shown_fc.assign(CLAIMS_COST=shown_fc["CLAIMS_COST"] * scale, CLAIM_COUNT=shown_fc["CLAIM_COUNT"] * scale)
    out = build_rows(shown_history, shown_fc, model, monthly_wape, rel_sd, seasonal_index(history["CLAIMS_COST"]))
    path = FACTS_DIR / "fact_forecast_seasonal.csv"
    out.to_csv(path, index=False, encoding="utf-8")
    _log(f"wrote {path} ({len(out)} rows: {len(history)} actual months, forecast {fc.index[0]} to {fc.index[-1]})")

    write_chart(history, fc, rel_sd)
    write_report(history, fc, scores, model, checks, dev, data_end)
    _log("Done.")


if __name__ == "__main__":
    main()
