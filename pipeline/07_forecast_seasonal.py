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
       still move. Each accident quarter is grossed up by its ultimate over what the warehouse
       triangles (sql/claims_dev_*.sql) have recorded to the data end: incurred cost with
       Bornhuetter-Ferguson (Cape Cod expected loss ratio), reported counts with chain-ladder.
     - without triangles (synthetic data: ULTIMATE_CLAIM already includes IBNR) the claims
       are used as they are
  3. Rolling validation: both models forecast from every quarter-end with two years of
     history, exactly as the live forecast is made; the lower mean WAPE over premium, count
     and cost at 3 / 6 / 12 months picks the model. The P10-P90 bands on premium and claims
     come from its monthly actual / forecast ratios, on the basis the page shows, per group of
     months ahead (1-3, 4-6, 7-12), each calibrated so its out-of-sample coverage (each origin
     against the others' ratios) is nearest 80% (plan H7).
  4. Checks with pass rules: accuracy vs seasonal naive, bias, band coverage, development
     look-back (factors from a year ago vs what then happened), and input reconciliations
     (premium rebuilt from policies, triangle vs claims extract)
  5. Forecast: the chosen model, from the full history

Outputs
-------
  <facts>/fact_forecast_seasonal.csv   actual + forecast months (3M / 6M / 12M horizons)
  <facts>/fact_forecast_validation.csv every check: value, rule, PASS / REVIEW
  <facts>/fact_forecast_known.csv      claims recorded so far and policy time run, by month (chart tooltip)
  <facts>/fact_forecast_total_miss.csv actual / forecast of each origin's horizon totals (card P10-P90 ranges)
  <reports>/seasonal_forecast_report.md
  <reports>/forecast_validation.png
"""

from __future__ import annotations

import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from settings import S, year_basis, expected_claims_basis, current_year
from development import DEV_DIAGONALS, TRIANGLES, cdf, cumulative, valuation_quarter
import premium_drivers
import timesfm_model
import training_window

warnings.filterwarnings("ignore")

FACTS_DIR = S.facts_dir
OUTPUT_DIR = S.reports_dir

FORECAST_HORIZONS = [3, 6, 12]          # months; one set of rows per horizon for the page slicer
# Rolling validation (method constants): forecast from every quarter-end with at least two
# years of history (two seasonal cycles) and score each horizon where the actuals exist.
MIN_CONTEXT_MONTHS = 24
VALIDATION_STEP_MONTHS = 3
# Method constants: the page's months get a seasonal shape of SHAPE_WEIGHTS (chosen each run by the
# backtest), from each calendar month's share of the year over the last SHAPE_YEARS years. Sales follow
# a partly repeating cycle (an Aug-Oct peak whose size moves), so a damped shape can beat both the flat
# path and a full copy of last year (2026-11-11 test: 3M premium error 25.0% flat, 22.4% at 50%).
SHAPE_WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
SHAPE_YEARS = 3
# Method constants (plan C4): claims above the 99.5th percentile of claim size over the last 5 years are
# a large-loss excess. The model can learn its claims rate from capped claims plus an even load (the
# excess / capped ratio of its own history), kept only when that beats the raw rate on the backtest.
LARGE_LOSS_QUANTILE = 0.995
LARGE_LOSS_YEARS = 5
# Method constant: the training window is chosen on the latest 12 quarter-end origins (three years),
# because the job is to forecast the next year, not 2012 (pipeline/training_window.py).
WINDOW_TEST_ORIGINS = 12
# Validation pass rules (method constants, shown with every result in the report and table)
BIAS_TOLERANCE = 0.10                   # |bias| above this over all origins -> REVIEW
COVERAGE_RANGE = (0.70, 0.90)           # share of months inside the P10-P90 band
DEV_LOOKBACK_QUARTERS = 4               # development check: factors from one year ago ...
DEV_LOOKBACK_AQS = 8                    # ... applied to the 8 accident quarters then most recent
DEV_NEWEST_TESTS = 8                    # newest-quarter check: valuations 4 ... 11 quarters ago
DEV_TOLERANCE = 0.05                    # |actual / expected development - 1| above this -> REVIEW
INPUT_TOLERANCE = 0.01                  # input reconciliations must agree within 1%
# Method constant: the book needs one policy year after the first policy is written before
# earned premium (and the claims on it) stop ramping up mechanically.
BOOK_RAMP_MONTHS = 12
# Method constants: claims cost is developed with Bornhuetter-Ferguson, claim counts with
# chain-ladder. BF gives a young accident quarter its recorded claims plus the share still to
# come times premium x an expected loss ratio, instead of scaling its few early claims by a large
# factor. The expected loss ratio is Cape Cod over the latest CAPE_COD_QUARTERS accident quarters
# (recorded claims / premium already "used up" by development), so it comes from the data.
# Evidence (Oracle, 2026-11-10, 12 past valuations): on the newest quarter chain-ladder
# over-predicted cost by +8.7% (mean abs error 11.2%), BF by +4.0% (9.7%). For counts chain-ladder
# was better (7.2% vs 8.4%+), so counts keep it.
BF_SERIES = {"CLAIMS_COST"}
CAPE_COD_QUARTERS = 12
# Plan H7: bands by months ahead, calibrated. Each group of months ahead (the page's horizons) gets its own band,
# and its tail share is the one whose out-of-sample coverage (each origin tested on a band from the others) is
# nearest CALIBRATION_TARGET, so "P10-P90" holds 80% of months in fact, not only in name. Method constants.
STEP_GROUPS = ((1, 3), (4, 6), (7, 12))
CALIBRATION_TARGET = 0.80
TAIL_GRID = tuple(round(t, 2) for t in np.arange(0.02, 0.255, 0.01))   # tail share below P10 (and above P90)

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
    # Plan C4: claims above the large-loss cap, kept apart so the model can learn a steadier rate
    recent = cl[cl["ACC_DATE"] > cl["ACC_DATE"].max() - pd.DateOffset(years=LARGE_LOSS_YEARS)]["ULTIMATE_CLAIM"]
    cap = float(recent[recent > 0].quantile(LARGE_LOSS_QUANTILE))
    cl = cl.assign(EXCESS=(cl["ULTIMATE_CLAIM"] - cap).clip(lower=0.0))
    claims = cl.groupby(month).agg(CLAIM_COUNT=("CLM_NO", "nunique"),
                                   CLAIMS_COST=("ULTIMATE_CLAIM", "sum"), CLAIMS_EXCESS=("EXCESS", "sum"))
    monthly = premium.to_frame().join(claims, how="left").fillna(0.0)
    monthly.attrs["large_loss_cap"] = cap
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
_cumulative, _cdf = cumulative, cdf          # shared with step 14 (pipeline/development.py)


def _cape_cod_elr(aqs: pd.PeriodIndex, known: np.ndarray, used: np.ndarray, start_q: pd.Period) -> float:
    """Cape Cod expected loss ratio: recorded claims / premium already used up by development
    (premium / factor to ultimate), over the latest CAPE_COD_QUARTERS accident quarters of the
    modelling window."""
    sel = np.where(aqs >= start_q)[0][-CAPE_COD_QUARTERS:]
    den = used[sel].sum()
    return float(known[sel].sum() / den) if den > 0 else np.nan


class _Dev:
    """Development as at one valuation quarter: chain-ladder factors, plus BF when given premium."""

    def __init__(self, tri: pd.DataFrame, value_col: str, valued: pd.Period,
                 premium_q: pd.Series | None, start_q: pd.Period | None):
        self.aqs, self.cum, self.age = _cumulative(tri, value_col, valued)
        self.cdf = _cdf(self.cum, self.age)
        self.known = self.cum[np.arange(len(self.aqs)), self.age]
        self.bf = premium_q is not None
        if self.bf:
            self.premium = premium_q.reindex(self.aqs).fillna(0.0).to_numpy(dtype=float)
            self.elr = _cape_cod_elr(self.aqs, self.known, self.premium / self.cdf[self.age], start_q)
            self.bf = np.isfinite(self.elr)

    def expected(self, i: int, later_age: int) -> float:
        """Expected cumulative value of accident quarter i once it reaches `later_age`
        (len(cdf) - 1 or more = ultimate)."""
        g, later = self.age[i], min(later_age, len(self.cdf) - 1)
        if self.bf:
            return self.known[i] + self.premium[i] * self.elr * (1 / self.cdf[later] - 1 / self.cdf[g])
        return self.known[i] * self.cdf[g] / self.cdf[later]


def development(tri: pd.DataFrame, value_col: str, last_q: pd.Period,
                premium_q: pd.Series | None = None, start_q: pd.Period | None = None):
    """
    Chain-ladder on a triangle of increments by accident x development quarter, or
    Bornhuetter-Ferguson when quarterly earned premium is given. Returns the chain-ladder
    factor to ultimate by age in quarters (index 0 = the latest complete quarter), per accident
    quarter the ratio ultimate / everything recorded so far, and the BF expected loss ratio (NaN
    for chain-ladder). The ratio is what the claims extract needs: it was taken at the data end,
    part-way into the next quarter, so it is further developed than the last complete diagonal.
    """
    recorded = tri.groupby(pd.to_datetime(tri["ACC_QUARTER"]).dt.to_period("Q"))[value_col].sum()
    d = _Dev(tri, value_col, last_q, premium_q, start_q)
    ultimate = pd.Series([d.expected(i, len(d.cdf)) for i in range(len(d.aqs))], index=d.aqs)
    ratio = (ultimate / recorded.reindex(d.aqs)).where(lambda r: np.isfinite(r) & (r > 0), 1.0)
    return d.cdf, ratio, (d.elr if d.bf else np.nan)


def development_lookback(tri: pd.DataFrame, value_col: str, last_q: pd.Period,
                         premium_q: pd.Series | None = None, start_q: pd.Period | None = None) -> pd.DataFrame:
    """
    Actual vs expected development: the method as at DEV_LOOKBACK_QUARTERS ago predicts how
    much each accident quarter would grow since then; compare with what it actually did.
    """
    then = _Dev(tri, value_col, last_q - DEV_LOOKBACK_QUARTERS, premium_q, start_q)
    aqs_now, cum_now, age_now = _cumulative(tri, value_col, last_q)
    rows = []
    for i in range(len(then.aqs) - DEV_LOOKBACK_AQS, len(then.aqs)):
        j = aqs_now.get_loc(then.aqs[i])
        rows.append({"ACC_QUARTER": str(then.aqs[i]), "KNOWN_THEN": then.known[i],
                     "EXPECTED_NOW": then.expected(i, then.age[i] + DEV_LOOKBACK_QUARTERS),
                     "ACTUAL_NOW": cum_now[j, age_now[j]]})
    out = pd.DataFrame(rows)
    out["ACTUAL_VS_EXPECTED"] = out["ACTUAL_NOW"] / out["EXPECTED_NOW"]
    return out


def development_newest(tri: pd.DataFrame, value_col: str, last_q: pd.Period,
                       premium_q: pd.Series | None = None, start_q: pd.Period | None = None) -> pd.DataFrame:
    """
    The newest accident quarter carries the largest factor and drives the forecast, so it is
    tested on its own: at each of DEV_NEWEST_TESTS past valuations, the method then predicts
    how far that quarter would grow by now; compare with what it actually reached.
    """
    aqs_now, cum_now, age_now = _cumulative(tri, value_col, last_q)
    rows = []
    for lag in range(DEV_LOOKBACK_QUARTERS, DEV_LOOKBACK_QUARTERS + DEV_NEWEST_TESTS):
        d = _Dev(tri, value_col, last_q - lag, premium_q, start_q)
        j = aqs_now.get_loc(d.aqs[-1])
        rows.append({"VALUED_AT": str(last_q - lag),
                     "ACTUAL_VS_EXPECTED": cum_now[j, age_now[j]] / d.expected(len(d.aqs) - 1, lag)})
    return pd.DataFrame(rows)


def develop(monthly: pd.DataFrame, data_end: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Gross claim count and cost up to ultimate. Returns the frame and a log of what was done."""
    out = monthly.copy()
    out["REPORTED_COST"] = out["CLAIMS_COST"]
    out["REPORTED_COUNT"] = out["CLAIM_COUNT"]

    files = TRIANGLES
    paths = {k: S.raw_dir / f"{name}.parquet" for k, (name, _) in files.items()} if S.raw_dir else {}
    if not paths or not all(p.exists() for p in paths.values()):
        if S.source == "oracle":
            _log("WARNING: claim triangles missing from the raw folder — recent months are NOT "
                 "developed (run 00_extract_oracle.py to pull sql/claims_dev_*.sql)")
        else:
            _log("no claim triangles for this source — ULTIMATE_CLAIM used as already developed")
        return out, {"developed": False}

    last_q = valuation_quarter(data_end)
    info = {"developed": True, "valuation_quarter": str(last_q), "cdf": {}, "latest_ratio": {},
            "lookback": {}, "newest": {}, "tri_vs_extract": {}, "method": {}, "elr": {}}
    premium_q = out["EARNED_PREMIUM"].groupby(out.index.asfreq("Q")).sum()
    start_q = (out.index.min() + BOOK_RAMP_MONTHS).asfreq("Q")   # the book's ramp-up is not experience
    for col, (name, value_col) in files.items():
        tri = pd.read_parquet(paths[col])
        bf = (premium_q, start_q) if col in BF_SERIES else (None, None)
        cdf, ratio, elr = development(tri, value_col, last_q, *bf)
        # Accident quarters outside the triangle (before its history, or the partial quarter
        # after last_q, which the forecast window never uses) stay as recorded.
        factor = ratio.reindex(out.index.asfreq("Q")).fillna(1.0).to_numpy()
        out[col] = out[col] * factor
        if col == "CLAIMS_COST" and "CLAIMS_EXCESS" in out:      # the large-loss part develops with the rest
            out["CLAIMS_EXCESS"] = out["CLAIMS_EXCESS"] * factor
        info["cdf"][col] = cdf[:8]
        info["method"][col] = "Bornhuetter-Ferguson" if np.isfinite(elr) else "chain-ladder"
        info["elr"][col] = elr
        info["latest_ratio"][col] = float(ratio.iloc[-1])
        info["lookback"][col] = development_lookback(tri, value_col, last_q, *bf)
        info["newest"][col] = development_newest(tri, value_col, last_q, *bf)
        # Same claims, two extracts: the triangle and fact_claim must agree on recent quarters
        quarters = pd.period_range(last_q - 7, last_q, freq="Q")
        in_tri = tri.groupby(pd.to_datetime(tri["ACC_QUARTER"]).dt.to_period("Q"))[value_col].sum()
        reported = "REPORTED_COST" if col == "CLAIMS_COST" else "REPORTED_COUNT"
        in_extract = out[reported].groupby(out.index.asfreq("Q")).sum()
        info["tri_vs_extract"][col] = float(in_tri.reindex(quarters).sum() / in_extract.reindex(quarters).sum())
        how = (f"Bornhuetter-Ferguson, Cape Cod expected loss ratio {elr:.1%}" if np.isfinite(elr)
               else "chain-ladder")
        _log(f"{name}: {how}; factor to ultimate {cdf[0]:.3f} at quarter-end; the latest quarter as "
             f"extracted x{ratio.iloc[-1]:.3f}, one year back x{ratio.iloc[-5]:.3f}")
    return out, info


# =========================================================================
# Premium: run-off of written policies + new business
# =========================================================================
# Car-years by written x earned month (plans C2 / C3), filled once in main(); kept apart from the premium
# cohorts so their many slices do not copy it.
CAR_COHORTS: dict[str, pd.DataFrame] = {}


def premium_cohorts(cars: bool = False) -> pd.DataFrame:
    """
    Earned premium by written month (rows) x earned month (columns): each policy's premium
    split over the months it is in force, pro rata to days, as in fact_earned_premium.
    With `cars`, the same for car-years in force (policy days / 365.25) instead of premium.
    """
    p = pd.read_csv(FACTS_DIR / "fact_policy.csv",
                    usecols=["CLASS_DESC", "POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE", "TOTAL_PREMIUM"],
                    parse_dates=["POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE"], low_memory=False)
    # Same classes as fact_earned_premium (its builder may use another class list than this run)
    classes = pd.read_csv(FACTS_DIR / "fact_earned_premium.csv", usecols=["CLASS_DESC"])["CLASS_DESC"].unique()
    p = p[p["CLASS_DESC"].isin(classes) & (p["POLICY_EXPIRY_DATE"] > p["POLICY_EFFECTIVE_DATE"])]
    p = p.assign(N=1.0)
    # Policies sharing both dates earn identically: total them first (millions -> thousands)
    g = p.groupby(["POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE"])[["TOTAL_PREMIUM", "N"]].sum().reset_index()

    eff = g["POLICY_EFFECTIVE_DATE"].to_numpy("datetime64[D]")
    exp = g["POLICY_EXPIRY_DATE"].to_numpy("datetime64[D]")
    per_day = (g["N"].to_numpy(dtype=float) / 365.25 if cars
               else g["TOTAL_PREMIUM"].to_numpy(dtype=float) / (exp - eff).astype(np.int64))
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


def forecast(model: str, history: pd.DataFrame, cohorts: pd.DataFrame, horizon: int,
             drivers=None, large_loss: bool = False, claims: str = "rate") -> pd.DataFrame:
    """
    Written premium and claims per unit of earned premium are forecast; earned premium comes
    from the run-off plus new business, and claims = rate x earned premium, so the claims
    follow the book's known growth or shrinkage instead of extending the past trend.
    With `drivers` (premium_drivers.Drivers, plan B1) written premium = renewals of the policies
    expiring each month (known at the origin) + new business, and the model forecasts new business.
    `claims` (plan C3, CLAIMS_METHODS): "rate" forecasts the cost per riyal of premium directly;
    "split" forecasts claims per riyal and the cost per claim, each on its own; "per_car" forecasts
    claims per car-year, the cost per claim and the premium per car-year; "book_cars" (plan C2) forecasts
    claims per car-year and the cost per claim, times the car-years the book will earn.
    """
    cost = history["CLAIMS_COST"]
    if large_loss and "CLAIMS_EXCESS" in history:
        # Plan C4: capped claims plus the large-loss excess spread evenly (its share of this history)
        capped = cost - history["CLAIMS_EXCESS"]
        cost = capped * (1 + history["CLAIMS_EXCESS"].sum() / max(capped.sum(), 1e-9))
    h = history.assign(CLAIM_RATE=history["CLAIM_COUNT"] / history["EARNED_PREMIUM"],
                       COST_RATE=cost / history["EARNED_PREMIUM"])
    series = [h[s] for s in MODELLED]
    if claims != "rate":
        # severity of months without claims: the median, so the series stays continuous
        severity = (cost / h["CLAIM_COUNT"].where(h["CLAIM_COUNT"] > 0)).pipe(lambda s: s.fillna(s.median()))
        series[MODELLED.index("COST_RATE")] = severity
        if claims in ("per_car", "book_cars"):
            car_years = h["CAR_YEARS"].where(h["CAR_YEARS"] > 0)
            series[MODELLED.index("CLAIM_RATE")] = (h["CLAIM_COUNT"] / car_years).fillna(0.0)
        if claims == "per_car":
            series.append((h["EARNED_PREMIUM"] / car_years).pipe(lambda s: s.fillna(s.median())))
    channels = pd.DataFrame()
    if drivers is not None:
        series[MODELLED.index("WRITTEN_PREMIUM")] = drivers.new_business(h["WRITTEN_PREMIUM"])
        if drivers.by_channel:            # plan B2: each sales channel's new business on its own
            channels = drivers.new_business_channels(h.index)
    batch = series + [channels[c] for c in channels.columns]
    if model == "timesfm":
        preds = timesfm_model.predict([s.to_numpy() for s in batch], horizon)
    else:
        preds = [seasonal_naive(s, horizon) for s in batch]
    index = pd.period_range(history.index[-1] + 1, periods=horizon, freq="M")
    fc = pd.DataFrame({s: np.clip(p, 0, None) for s, p in zip(MODELLED, preds)}, index=index)
    if claims in ("split", "per_car"):        # book_cars waits for the earned car-years below
        severity = fc["COST_RATE"]
        if claims == "per_car":         # claims per riyal = claims per car-year / premium per car-year
            fc["CLAIM_RATE"] = fc["CLAIM_RATE"] / np.clip(preds[len(MODELLED)], 1e-9, None)
        fc["COST_RATE"] = fc["CLAIM_RATE"] * severity
    if drivers is not None:
        fc["NEW_BUSINESS"] = fc["WRITTEN_PREMIUM"]
        if len(channels.columns):
            for c, p in zip(channels.columns, preds[len(series):]):
                fc[f"NB_{c}"] = np.clip(p, 0, None)
            fc["NEW_BUSINESS"] = fc[[f"NB_{c}" for c in channels.columns]].sum(axis=1)
        fc["RENEWALS"] = drivers.renewals(history.index[-1], index).to_numpy()
        fc["WRITTEN_PREMIUM"] = fc["NEW_BUSINESS"] + fc["RENEWALS"]
    origin = history.index[-1]
    fc["EARNED_PREMIUM"], fc["RUNOFF_PREMIUM"] = earned_premium(cohorts, origin, fc["WRITTEN_PREMIUM"])
    if claims == "book_cars":
        # Plan C2: cars from the book. Car-years of the policies already written run off exactly (their dates);
        # new policies bring the car-years per riyal written in the 12 months to the origin, earned the same way.
        cars = CAR_COHORTS["cars"]
        per_riyal = cars.loc[origin - 11:origin].to_numpy().sum() / max(cohorts.loc[origin - 11:origin].to_numpy().sum(), 1e-9)
        car_years, _ = earned_premium(cars, origin, fc["WRITTEN_PREMIUM"] * per_riyal)
        fc["CLAIM_RATE"] = fc["CLAIM_RATE"] * car_years / fc["EARNED_PREMIUM"].replace(0, np.nan)
        fc["COST_RATE"] = fc["CLAIM_RATE"] * fc["COST_RATE"]          # COST_RATE holds the cost per claim here
    fc["CLAIM_COUNT"] = fc["CLAIM_RATE"] * fc["EARNED_PREMIUM"]
    fc["CLAIMS_COST"] = fc["COST_RATE"] * fc["EARNED_PREMIUM"]
    return fc


# =========================================================================
# Rolling validation
# =========================================================================
def backtest_origins(history: pd.DataFrame) -> list[pd.Period]:
    """Quarter-ends with MIN_CONTEXT_MONTHS of history before them and at least the shortest
    horizon of actuals after them."""
    last = history.index[-1]
    return [o for o in history.index[MIN_CONTEXT_MONTHS - 1:]
            if o.month % VALIDATION_STEP_MONTHS == 0 and o + min(FORECAST_HORIZONS) <= last]


def rolling_backtest(history: pd.DataFrame, cohorts: pd.DataFrame, models: list[str],
                     window_months: int | None = None, origins: list | None = None,
                     drivers=None, large_loss: bool = False, claims: str = "rate") -> pd.DataFrame:
    """
    Forecast from every quarter-end origin (or the given ones), exactly as the live forecast is
    made: the model sees the history up to the origin, or only its last `window_months`.
    One row per origin x model x month ahead, with forecast and actual for each series.
    """
    rows = []
    for o in origins if origins is not None else backtest_origins(history):
        actual = history.loc[o + 1:o + max(FORECAST_HORIZONS)]
        context = history.loc[:o] if window_months is None else history.loc[o - window_months + 1:o]
        for m in models:
            pred = forecast(m, context, cohorts, max(FORECAST_HORIZONS), drivers, large_loss,
                            claims).loc[actual.index]
            for step, month in enumerate(actual.index, start=1):
                rows.append({"ORIGIN": o, "MODEL": m, "STEP": step, "MONTH": month,
                             **{f"PRED_{s}": pred.at[month, s] for s in SERIES + ["WRITTEN_PREMIUM", "COST_RATE"]},
                             **{f"ACT_{s}": actual.at[month, s] for s in SERIES}})
    return pd.DataFrame(rows)


def origin_errors(bt: pd.DataFrame) -> np.ndarray:
    """
    One error per forecast origin, on what the page and the prices use: the 3, 6 and 12-month
    totals of premium and claims (absolute error / actual) and the 12-month loss ratio (absolute
    error / actual), averaged. The trend factor and the cards rest on these, not on single months.
    """
    out = []
    for _, g in bt.groupby("ORIGIN"):
        terms = []
        for h in FORECAST_HORIZONS:
            w = g[g["STEP"] <= h]
            if len(w) < h:
                continue
            for s in ("EARNED_PREMIUM", "CLAIMS_COST"):
                terms.append(abs(w[f"PRED_{s}"].sum() / w[f"ACT_{s}"].sum() - 1))
            if h == max(FORECAST_HORIZONS):
                pred = w["PRED_CLAIMS_COST"].sum() / w["PRED_EARNED_PREMIUM"].sum()
                act = w["ACT_CLAIMS_COST"].sum() / w["ACT_EARNED_PREMIUM"].sum()
                terms.append(abs(pred / act - 1))
        if terms:
            out.append(float(np.mean(terms)))
    return np.array(out)


def select_window(history: pd.DataFrame, cohorts: pd.DataFrame, models: list[str], origins: list | None = None,
                  record: bool = True) -> int | None:
    """
    How many years of history the forecast learns from (pipeline/training_window.py): every
    candidate forecasts from the same latest WINDOW_TEST_ORIGINS origins (or the given ones), seeing only
    its own window before each, and is scored per origin by origin_errors. Seasonal naive uses one year
    whatever the window, so the test uses the model. record=False (the holdout check) applies the same
    rule without writing the decision.
    """
    model = models[0]
    origins = backtest_origins(history)[-WINDOW_TEST_ORIGINS:] if origins is None else origins
    errors = {}
    for w in training_window.candidates(len(history) / 12):
        months = None if w is None else 12 * w
        if months is not None and months < MIN_CONTEXT_MONTHS:
            continue
        errors[w] = origin_errors(rolling_backtest(history, cohorts, [model], months, origins))
    if not record:
        setting = training_window.configured()
        return training_window.choose(errors)[0] if setting == "auto" else setting
    return training_window.resolve(
        "forecast", errors, f"{LABEL[model]}: error of 3/6/12-month totals and 12-month loss ratio, "
        f"latest {len(origins)} origins")


# Plan H6: the latest HOLDOUT_ORIGINS origins with 12 months of actuals are kept out of every choice; the whole
# choice is made again on the WINDOW_TEST_ORIGINS origins whose 12 months end before the first of them, and the
# result is scored on the held-out ones. Method constant.
HOLDOUT_ORIGINS = 4


def holdout_check(full_history: pd.DataFrame, cohorts: pd.DataFrame, models: list[str], split, live: dict) -> dict:
    """
    Plan H6: honest accuracy. Every choice the live run makes (training window, model, premium method, large-loss
    cap, claims method) is made again on earlier origins only, and the configuration it picks is scored on the
    latest origins, which no choice saw; repeating last year is scored on the same origins. The live run's own
    error on the origins it chose on is reported beside it: the gap shows how flattering the in-sample figure is.
    """
    import copy
    last = full_history.index[-1]
    full12 = [o for o in backtest_origins(full_history) if o + max(FORECAST_HORIZONS) <= last]
    hold = full12[-HOLDOUT_ORIGINS:]
    if len(hold) < 2:
        return {"STATUS": "not enough history for a holdout"}
    chooser = [o for o in backtest_origins(full_history) if o + max(FORECAST_HORIZONS) <= hold[0]][-WINDOW_TEST_ORIGINS:]
    if len(chooser) < 4:
        return {"STATUS": f"only {len(chooser)} origins before the holdout: not enough to choose on"}
    _log(f"holdout: choosing again on {chooser[0]}..{chooser[-1]}, scoring on {hold[0]}..{hold[-1]}")
    window = select_window(full_history, cohorts, models, chooser, record=False)
    months = None if window is None else 12 * window
    bt = rolling_backtest(full_history, cohorts, models, months, chooser)
    model = score(bt).groupby("MODEL")["WAPE"].mean().idxmin()
    drivers, premium_method = None, "total"
    if split is not None and split.available:
        d = copy.copy(split)
        premium_method, _ = select_premium_method(full_history, cohorts, model, months, bt, d)
        d.by_channel = premium_method == "channels"
        drivers = d if premium_method != "total" else None
        if drivers is not None:
            bt = rolling_backtest(full_history, cohorts, models, months, chooser, drivers)
    large_loss, _ = select_large_loss(full_history, cohorts, model, months, chooser, drivers, bt)
    if large_loss:
        bt = rolling_backtest(full_history, cohorts, models, months, chooser, drivers, True)
    claims, _ = select_claims_method(full_history, cohorts, model, months, chooser, drivers, large_loss, bt)
    test = rolling_backtest(full_history, cohorts, sorted({model, "seasonal_naive"}), months, hold, drivers,
                            large_loss, claims)
    err = {m: origin_errors(test[test["MODEL"] == m]) for m in test["MODEL"].unique()}
    out = {"STATUS": "ok", "CHOSEN_ON": f"{chooser[0]}..{chooser[-1]}", "HOLDOUT": f"{hold[0]}..{hold[-1]}",
           "N_HOLDOUT": len(hold), "WINDOW": training_window.label(window), "MODEL": LABEL[model],
           "PREMIUM_METHOD": premium_method, "LARGE_LOSS": "capped + load" if large_loss else "raw claims",
           "CLAIMS_METHOD": CLAIMS_METHODS[claims],
           "HOLDOUT_ERROR": float(np.mean(err[model])), "NAIVE_HOLDOUT_ERROR": float(np.mean(err["seasonal_naive"])),
           "LIVE_IN_SAMPLE_ERROR": live["IN_SAMPLE_ERROR"], "LIVE_CONFIG": live["CONFIG"],
           "SAME_CONFIG": live["CONFIG"] == f"{training_window.label(window)} / {LABEL[model]} / {premium_method} / "
                                             f"{'capped + load' if large_loss else 'raw claims'} / {CLAIMS_METHODS[claims]}"}
    _log(f"holdout: chosen on earlier origins: {out['WINDOW']} / {out['MODEL']} / {premium_method} / {out['LARGE_LOSS']} / "
         f"{out['CLAIMS_METHOD']}; error on the {len(hold)} held-out origins {out['HOLDOUT_ERROR']:.1%} vs repeating "
         f"last year {out['NAIVE_HOLDOUT_ERROR']:.1%} (the live run's in-sample error {out['LIVE_IN_SAMPLE_ERROR']:.1%})")
    return out


def month_shares(series: pd.Series, origin: pd.Period) -> pd.Series | None:
    """Each calendar month's share of the year, averaged over the last SHAPE_YEARS full 12-month
    blocks up to `origin` (None with fewer than two blocks)."""
    blocks = []
    for k in range(SHAPE_YEARS):
        b = series.loc[origin - 12 * (k + 1) + 1:origin - 12 * k]
        if len(b) == 12 and b.sum() > 0:
            blocks.append((b / b.sum()).groupby(b.index.month).sum())
    if len(blocks) < 2:
        return None
    s = pd.concat(blocks, axis=1).mean(axis=1)
    return s / s.sum()


def shape_path(path: pd.Series, shares: pd.Series | None, weight: float) -> pd.Series:
    """Redistribute a forecast path's total over its months: `weight` x the seasonal shares plus
    (1 - weight) x the model's own shape. The total never changes."""
    if shares is None or weight == 0 or path.sum() == 0:
        return path
    s = shares.reindex(path.index.month).to_numpy()
    return path.sum() * (weight * s / s.sum() + (1 - weight) * path.to_numpy() / path.sum())


def shaped_backtest(bt: pd.DataFrame, model: str, shown: pd.DataFrame, weight: float) -> pd.DataFrame:
    """The page's written premium and claims of each past origin, reshaped like the live forecast."""
    b = bt[bt["MODEL"] == model].copy()
    if weight == 0:
        return b
    for o, idx in b.groupby("ORIGIN").groups.items():
        g = b.loc[idx].set_index("MONTH")
        prem = shape_path(g["PRED_WRITTEN_PREMIUM"], month_shares(shown["EARNED_PREMIUM"], o), weight)
        claims = shape_path(g["PRED_COST_RATE"] * g["PRED_WRITTEN_PREMIUM"], month_shares(shown["CLAIMS_COST"], o), weight)
        b.loc[idx, "PRED_WRITTEN_PREMIUM"] = np.asarray(prem)
        b.loc[idx, "PRED_COST_RATE"] = np.asarray(claims) / np.asarray(prem)
    return b


def shape_errors(b: pd.DataFrame, shown: pd.DataFrame) -> np.ndarray:
    """One error per origin, on the page's basis: the 3 and 6-month totals (cards) and the
    month-by-month error (chart), premium and claims, averaged."""
    out = []
    for _, g in b.groupby("ORIGIN"):
        act = shown.reindex(g["MONTH"])
        p, c = g["PRED_WRITTEN_PREMIUM"].to_numpy(), (g["PRED_COST_RATE"] * g["PRED_WRITTEN_PREMIUM"]).to_numpy()
        ap, ac = act["EARNED_PREMIUM"].to_numpy(), act["CLAIMS_COST"].to_numpy()
        terms = [np.abs(p - ap).sum() / ap.sum(), np.abs(c - ac).sum() / ac.sum()]
        for h in (3, 6):
            if len(g) >= h:
                terms += [abs(p[:h].sum() / ap[:h].sum() - 1), abs(c[:h].sum() / ac[:h].sum() - 1)]
        out.append(float(np.mean(terms)))
    return np.array(out)


def select_shape(bt: pd.DataFrame, model: str, shown: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    """How much seasonal shape the page's months get. Each weight is scored per origin; the
    lowest mean error wins, and of the weights within one standard error of it the smallest is
    kept (less shape when the test cannot tell them apart)."""
    errs = {w: shape_errors(shaped_backtest(bt, model, shown, w), shown) for w in SHAPE_WEIGHTS}
    # Only the origins every weight can be scored on (a year without data gives no error), the same for all
    usable = np.logical_and.reduce([np.isfinite(e) for e in errs.values()])
    errs = {w: e[usable] for w, e in errs.items()}
    if usable.sum() < 2:
        _log(f"seasonal shape: only {int(usable.sum())} origin(s) can be scored; no shape (0%)")
        return 0.0, pd.DataFrame({"WEIGHT": list(SHAPE_WEIGHTS), "ERROR": np.nan, "STD_ERROR": np.nan,
                                  "N_ORIGINS": int(usable.sum()), "CHOSEN": [w == 0.0 for w in SHAPE_WEIGHTS]})
    mean = {w: e.mean() for w, e in errs.items()}
    best = min(mean, key=mean.get)
    limit = mean[best] + errs[best].std(ddof=1) / np.sqrt(len(errs[best]))
    weight = min(w for w in SHAPE_WEIGHTS if mean[w] <= limit)
    _log("seasonal shape: " + ", ".join(f"{w:.0%} {mean[w]:.1%}" for w in SHAPE_WEIGHTS)
         + f" -> {weight:.0%} (lowest {best:.0%}; smallest weight within one standard error)")
    table = pd.DataFrame({"WEIGHT": list(mean), "ERROR": list(mean.values()),
                          "STD_ERROR": [errs[w].std(ddof=1) / np.sqrt(len(errs[w])) for w in mean],
                          "N_ORIGINS": [len(errs[w]) for w in mean], "CHOSEN": [w == weight for w in mean]})
    return weight, table


def written_errors(bt: pd.DataFrame, history: pd.DataFrame) -> pd.Series:
    """One error per origin for written premium: 3 / 6 / 12-month totals and the monthly error, averaged."""
    out = {}
    for o, g in bt.groupby("ORIGIN"):
        p = g["PRED_WRITTEN_PREMIUM"].to_numpy()
        a = history["WRITTEN_PREMIUM"].reindex(g["MONTH"]).to_numpy()
        terms = [np.abs(p - a).sum() / a.sum()]
        terms += [abs(p[:h].sum() / a[:h].sum() - 1) for h in FORECAST_HORIZONS if len(g) >= h]
        out[o] = float(np.mean(terms))
    return pd.Series(out)


def select_premium_method(full_history, cohorts, model, window_months, bt, drivers) -> tuple[str, pd.DataFrame]:
    """Plan B1 against the total-premium forecast, on the same origins: the driver model is kept only
    when its written-premium error is lower by more than its standard error (G2 gate)."""
    nb_start = drivers.first_classified + MIN_CONTEXT_MONTHS - 1
    origins = [o for o in bt["ORIGIN"].unique() if o >= nb_start]
    if len(origins) < 4:
        _log(f"premium drivers: only {len(origins)} origins with {MIN_CONTEXT_MONTHS} months of new-business "
             "history; total-premium forecast kept")
        return "total", pd.DataFrame()
    import copy
    by_channel = copy.copy(drivers)
    by_channel.by_channel = True
    errors = {"total": written_errors(bt[(bt["MODEL"] == model) & bt["ORIGIN"].isin(origins)], full_history)}
    for name, d in (("drivers", copy.copy(drivers)), ("channels", by_channel)):
        if name == "drivers":
            d.by_channel = False
        errors[name] = written_errors(rolling_backtest(full_history, cohorts, [model], window_months, origins, d),
                                      full_history)
    se = {k: e.std(ddof=1) / np.sqrt(len(e)) for k, e in errors.items()}
    # from the simplest to the most detailed: a step is taken only when it wins by more than its noise
    method = "total"
    for nxt in ("drivers", "channels"):
        if errors[nxt].mean() < errors[method].mean() - se[nxt]:
            method = nxt
    table = pd.DataFrame({"METHOD": list(errors), "ERROR": [e.mean() for e in errors.values()],
                          "STD_ERROR": list(se.values()), "N_ORIGINS": len(origins),
                          "CHOSEN": [k == method for k in errors]})
    label = {"total": "total", "drivers": "renewals + new business", "channels": "renewals + new business by channel"}
    _log("premium method: " + ", ".join(f"{label[k]} {e.mean():.1%} ±{se[k]:.1%}" for k, e in errors.items())
         + f" over {len(origins)} origins -> {label[method]}")
    return method, table


def claims_errors(bt: pd.DataFrame) -> pd.Series:
    """One error per origin for claims, against the real (uncapped) claims: the monthly error, the
    3 / 6 / 12-month totals and the 12-month loss ratio, averaged."""
    out = {}
    for o, g in bt.groupby("ORIGIN"):
        p, a = g["PRED_CLAIMS_COST"].to_numpy(), g["ACT_CLAIMS_COST"].to_numpy()
        terms = [np.abs(p - a).sum() / a.sum()]
        terms += [abs(p[:h].sum() / a[:h].sum() - 1) for h in FORECAST_HORIZONS if len(g) >= h]
        if len(g) >= max(FORECAST_HORIZONS):
            lr_p = p.sum() / g["PRED_EARNED_PREMIUM"].sum()
            lr_a = a.sum() / g["ACT_EARNED_PREMIUM"].sum()
            terms.append(abs(lr_p / lr_a - 1))
        out[o] = float(np.mean(terms))
    return pd.Series(out)


def select_large_loss(full_history, cohorts, model, window_months, origins, drivers, bt) -> tuple[bool, pd.DataFrame]:
    """Plan C4: learn the claims rate from capped claims + an even large-loss load, or from the raw
    claims. Kept only when its error against the real claims is lower by more than its standard error."""
    b_raw = bt[(bt["MODEL"] == model) & bt["ORIGIN"].isin(origins)]
    b_cap = rolling_backtest(full_history, cohorts, [model], window_months, origins, drivers, True)
    e_raw, e_cap = claims_errors(b_raw), claims_errors(b_cap)
    se = lambda e: e.std(ddof=1) / np.sqrt(len(e))
    use = e_cap.mean() < e_raw.mean() - se(e_cap)
    table = pd.DataFrame({"METHOD": ["raw claims", "capped + load"], "ERROR": [e_raw.mean(), e_cap.mean()],
                          "STD_ERROR": [se(e_raw), se(e_cap)], "N_ORIGINS": len(origins), "CHOSEN": [not use, use]})
    _log(f"large-loss cap: raw {e_raw.mean():.1%} ±{se(e_raw):.1%}, capped + load {e_cap.mean():.1%} "
         f"±{se(e_cap):.1%} over {len(origins)} origins -> {'capped + load' if use else 'raw claims'}")
    return use, table


CLAIMS_METHODS = {"rate": "cost per riyal", "split": "claims per riyal x cost per claim",
                  "per_car": "claims per car-year x cost per claim / premium per car-year",
                  "book_cars": "claims per car-year x cost per claim x the book's car-years"}


def select_claims_method(full_history, cohorts, model, window_months, origins, drivers, large_loss,
                         bt) -> tuple[str, pd.DataFrame]:
    """Plan C3: claims cost forecast as one rate, or split into how many claims and what each costs
    (per riyal, then per car-year when the exposure is there). From the simplest, a step is taken only
    when its error against the real claims is lower by more than its standard error."""
    errors = {"rate": claims_errors(bt[(bt["MODEL"] == model) & bt["ORIGIN"].isin(origins)])}
    has_cars = full_history.get("CAR_YEARS", pd.Series(dtype=float)).gt(0).all() and "cars" in CAR_COHORTS
    candidates = ["split"] + (["per_car", "book_cars"] if has_cars else [])
    for name in candidates:
        errors[name] = claims_errors(rolling_backtest(full_history, cohorts, [model], window_months, origins,
                                                      drivers, large_loss, name))
    se = {k: e.std(ddof=1) / np.sqrt(len(e)) for k, e in errors.items()}
    method = "rate"
    for nxt in candidates:
        if errors[nxt].mean() < errors[method].mean() - se[nxt]:
            method = nxt
    table = pd.DataFrame({"METHOD": [CLAIMS_METHODS[k] for k in errors], "ERROR": [e.mean() for e in errors.values()],
                          "STD_ERROR": list(se.values()), "N_ORIGINS": len(origins),
                          "CHOSEN": [k == method for k in errors]})
    _log("claims method: " + ", ".join(f"{CLAIMS_METHODS[k]} {e.mean():.1%} ±{se[k]:.1%}" for k, e in errors.items())
         + f" over {len(origins)} origins -> {CLAIMS_METHODS[method]}")
    return method, table


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


def band_ratios(bt: pd.DataFrame, model: str, shown: pd.DataFrame, written: bool,
                claims_scale: float = 1.0) -> pd.DataFrame:
    """
    Actual / forecast by origin and month, on the basis the page shows: on written_year, written
    premium and claims at the forecast rate on it, against the page's actual months. Past claims
    forecasts take the same scale as the page's line, so its bias correction is not counted twice.
    """
    b = bt[bt["MODEL"] == model]
    prem = b["PRED_WRITTEN_PREMIUM"] if written else b["PRED_EARNED_PREMIUM"]
    actual = shown.reindex(b["MONTH"])
    out = pd.DataFrame({
        "ORIGIN": b["ORIGIN"].to_numpy(), "STEP": b["STEP"].to_numpy(),
        "PREMIUM": actual["EARNED_PREMIUM"].to_numpy() / prem.to_numpy(),
        "CLAIMS": actual["CLAIMS_COST"].to_numpy() / (b["PRED_COST_RATE"] * prem * claims_scale).to_numpy()})
    out["LOSS_RATIO"] = out["CLAIMS"] / out["PREMIUM"]      # actual / forecast loss ratio, same months
    return out


def _coverage(r: pd.Series, origins: np.ndarray, tail: float) -> float:
    """Share of months inside the band, each origin's months checked against a band from the other origins."""
    inside = []
    for o in pd.unique(origins):
        own = origins == o
        if (~own).sum() < 2:
            continue
        low, high = r[~own].quantile((tail, 1 - tail))
        inside.extend(r[own].dropna().between(low, high).tolist())
    return float(np.mean(inside)) if inside else np.nan


def band(ratios: pd.DataFrame, series: str) -> dict:
    """
    Plan H7: one series' band per group of months ahead (STEP_GROUPS), calibrated: the tail share whose
    out-of-sample coverage is nearest CALIBRATION_TARGET (on a tie the narrower band), then that share's
    quantiles of all origins' actual / forecast ratios. Returns {(first, last): (low, high, coverage, tail)}.
    """
    out = {}
    for first, last in STEP_GROUPS:
        g = ratios[ratios["STEP"].between(first, last)]
        r = g[series].replace([np.inf, -np.inf], np.nan).reset_index(drop=True)
        origins = g["ORIGIN"].to_numpy()
        if r.notna().sum() < 4:
            continue
        cov = {t: _coverage(r, origins, t) for t in TAIL_GRID}
        tail = min((t for t in cov if not np.isnan(cov[t])), key=lambda t: (abs(cov[t] - CALIBRATION_TARGET), -t))
        low, high = r.quantile((tail, 1 - tail))
        out[(first, last)] = (float(low), float(high), cov[tail], tail)
    return out


def band_at(bands: dict, step: int) -> tuple[float, float]:
    """The (low, high) ratio for a month `step` months ahead; (nan, nan) when its group had too few months."""
    for (first, last), b in bands.items():
        if first <= step <= last:
            return b[0], b[1]
    return np.nan, np.nan


def total_misses(bt: pd.DataFrame, model: str, shown: pd.DataFrame, written: bool,
                 claims_scale: float = 1.0) -> pd.DataFrame:
    """
    Actual / forecast of each origin's 3 / 6 / 12-month totals, on the basis the page shows, for the
    Forecast cards' P10-P90 ranges. Monthly bands cannot be added up: months do not all miss the same
    way. Each measure is divided by its own median per horizon, so a range shows how far totals have
    missed around the card's figure; the median itself is the past bias (kept in the RAW_MEDIAN_ columns).
    Premium and claims stay paired by origin, so the page can derive the underwriting result per origin.
    """
    b = bt[bt["MODEL"] == model]
    prem = (b["PRED_WRITTEN_PREMIUM"] if written else b["PRED_EARNED_PREMIUM"]).to_numpy()
    actual = shown.reindex(b["MONTH"])
    months = pd.DataFrame({
        "ORIGIN": b["ORIGIN"].to_numpy(), "STEP": b["STEP"].to_numpy(),
        "F_PREMIUM": prem, "F_CLAIMS": b["PRED_COST_RATE"].to_numpy() * prem * claims_scale,
        "F_COUNT": (b["PRED_CLAIM_COUNT"] / b["PRED_EARNED_PREMIUM"]).to_numpy() * prem * claims_scale,
        "A_PREMIUM": actual["EARNED_PREMIUM"].to_numpy(), "A_CLAIMS": actual["CLAIMS_COST"].to_numpy(),
        "A_COUNT": actual["CLAIM_COUNT"].to_numpy()})
    rows = []
    for h in FORECAST_HORIZONS:
        full = months[months["STEP"] <= h].groupby("ORIGIN").filter(lambda g: len(g) == h)
        tot = full.drop(columns="STEP").groupby("ORIGIN").sum()
        raw = pd.DataFrame({s: tot[f"A_{s}"] / tot[f"F_{s}"] for s in ("PREMIUM", "CLAIMS", "COUNT")})
        raw["LOSS_RATIO"] = raw["CLAIMS"] / raw["PREMIUM"]
        raw["SEVERITY"] = raw["CLAIMS"] / raw["COUNT"]
        rows.append((raw / raw.median()).assign(HORIZON=f"{h}M", N_ORIGINS=len(raw),
                                                **{f"RAW_MEDIAN_{c}": raw[c].median() for c in raw}).reset_index())
    out = pd.concat(rows, ignore_index=True)
    out["ORIGIN"] = out["ORIGIN"].astype(str)
    return out


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
    step 05, which takes every year's ultimate from step 14 (reserving). A policy whose
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

    # Every year's months add up to its ultimate in segment results (step 14, reserving): each month starts
    # from what is recorded, so none sits below it, and the year's still-to-come goes to the months whose
    # share by the monthly shape (recorded for mature years, the model's for the others) is above it.
    recorded = {"CLAIMS_COST": cl.groupby("M")["ULTIMATE_CLAIM"].sum(), "CLAIM_COUNT": cl.groupby("M")["CLM_NO"].size()}
    for col, total in (("CLAIMS_COST", "ULTIMATE_CLAIMS"), ("CLAIM_COUNT", "CLAIM_COUNT")):
        for year, g in h.groupby(h.index.year):
            if year in year_totals.index and g[col].sum() > 0:
                year_total = year_totals.at[year, total]
                known = recorded[col].reindex(g.index, fill_value=0).astype(float)
                rest = (g[col] * year_total / g[col].sum() - known).clip(lower=0.0)
                # nothing still to come (ultimate = recorded): the months are what is recorded, not 0 / 0
                share = rest / rest.sum() if rest.sum() > 0 else 0.0
                h.loc[g.index, col] = known + share * max(year_total - known.sum(), 0.0)

    f = fc.copy()
    f["EARNED_PREMIUM"] = fc["WRITTEN_PREMIUM"]
    f["CLAIM_COUNT"] = fc["CLAIM_RATE"] * f["EARNED_PREMIUM"]
    f["CLAIMS_COST"] = fc["COST_RATE"] * f["EARNED_PREMIUM"]
    return h, f


def known_share(months: pd.PeriodIndex, data_end: pd.Timestamp) -> pd.DataFrame:
    """
    [report] year_basis = "written_year": how much of each month's claims figure is known, for the Forecast
    chart's tooltip. RECORDED_CLAIMS = claims recorded so far on the policies that started in the month, in
    the same month as written_month_view. TERM_RUN_SHARE = share of their premium whose policy time had run
    by the data end. CLAIMS_BASIS = "Estimate" for the months written_month_view takes from the year's estimate.
    """
    pp = pd.read_csv(FACTS_DIR / "fact_policy_pricing.csv",
                     usecols=["POLICY_NO", "POLICY_YEAR", "POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE", "TOTAL_PREMIUM"],
                     parse_dates=["POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE"], low_memory=False)
    eff = pp["POLICY_EFFECTIVE_DATE"]
    number = np.where(eff.dt.year == pp["POLICY_YEAR"], eff.dt.month, np.where(eff.dt.year < pp["POLICY_YEAR"], 1, 12))
    pp["M"] = pd.PeriodIndex(year=pp["POLICY_YEAR"], month=number, freq="M")

    cl = pd.read_csv(FACTS_DIR / "fact_claim.csv", usecols=["POLICY_NO", "POLICY_YEAR", "POLICY_MATCHED", "ULTIMATE_CLAIM"])
    cl = cl[cl["POLICY_MATCHED"].astype(str).str.lower() == "true"]
    cl = cl.merge(pp[["POLICY_NO", "POLICY_YEAR", "M"]].drop_duplicates(["POLICY_NO", "POLICY_YEAR"]), on=["POLICY_NO", "POLICY_YEAR"])

    term = (pp["POLICY_EXPIRY_DATE"] - eff).dt.days
    dated = pp[term > 0]
    run = ((data_end - eff).dt.days.clip(lower=0) / term[term > 0]).clip(upper=1)
    premium = dated.groupby("M")["TOTAL_PREMIUM"].sum()
    premium_run = (dated["TOTAL_PREMIUM"] * run.reindex(dated.index)).groupby(dated["M"]).sum()

    basis = pd.read_csv(FACTS_DIR / "fact_segment_performance_by_year.csv", usecols=["POLICY_YEAR", "CLAIMS_BASIS"])
    mature = set(basis.loc[basis["CLAIMS_BASIS"].str.startswith("Actual"), "POLICY_YEAR"])
    return pd.DataFrame({
        "MONTH_DATE": months.to_timestamp().strftime("%Y-%m-%d"),
        "CLAIMS_BASIS": np.where(months.year.isin(mature), "Actual", "Estimate"),
        "RECORDED_CLAIMS": cl.groupby("M")["ULTIMATE_CLAIM"].sum().reindex(months, fill_value=0.0).to_numpy(),
        "TERM_RUN_SHARE": (premium_run / premium).reindex(months).to_numpy(),
    })


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
    nxt = fc.head(12)
    raw = nxt["CLAIMS_COST"].sum() / nxt["EARNED_PREMIUM"].sum()
    # Plan C3: the raw change = claims per riyal change x cost per claim change (before the bias correction)
    freq = (nxt["CLAIM_COUNT"].sum() / nxt["EARNED_PREMIUM"].sum()) / (last12["CLAIM_COUNT"].sum() / last12["EARNED_PREMIUM"].sum())
    sev = (nxt["CLAIMS_COST"].sum() / nxt["CLAIM_COUNT"].sum()) / (last12["CLAIMS_COST"].sum() / last12["CLAIM_COUNT"].sum())
    return {"TREND_FACTOR": (raw - bias) / before, "TREND_FACTOR_RAW": raw / before, "BIAS_PTS": bias * 100,
            "N_ORIGINS": len(t), "ERROR_FORECAST_PTS": (t["PRED"] - t["ACT"]).abs().mean() * 100,
            "ERROR_CORRECTED_PTS": (t["PRED"] - bias - t["ACT"]).abs().mean() * 100,
            "ERROR_NO_CHANGE_PTS": (t["BEFORE"] - t["ACT"]).abs().mean() * 100,
            "DIRECTION_RIGHT": int((np.sign(t["PRED"] - t["BEFORE"]) == np.sign(t["ACT"] - t["BEFORE"])).sum()),
            "FREQUENCY_CHANGE": freq - 1, "SEVERITY_CHANGE": sev - 1}


def build_rows(history: pd.DataFrame, fc: pd.DataFrame, model: str, model_wape: float,
               bands: dict, season: pd.Series) -> pd.DataFrame:
    rows = []
    for m, r in history.iterrows():
        rows.append({
            "MONTH_DATE": m.to_timestamp(), "FORECAST_HORIZON": "ACTUAL", "PERIOD_TYPE": "actual",
            "FORECAST_CLAIMS": r["CLAIMS_COST"], "FORECAST_PREMIUM": r["EARNED_PREMIUM"],
            "CLAIMS_P10": np.nan, "CLAIMS_P90": np.nan, "PREMIUM_P10": np.nan, "PREMIUM_P90": np.nan,
            "LOSS_RATIO_P10": np.nan, "LOSS_RATIO_P90": np.nan,
            "FORECAST_RENEWALS": np.nan, "FORECAST_NEW_BUSINESS": np.nan,
            "FORECAST_CLAIM_COUNT": r["CLAIM_COUNT"],
            "SEASONAL_INDEX": 1.0, "MODEL_TYPE": "actual", "MODEL_WAPE": np.nan, "BIAS_CORRECTION": np.nan,
        })
    for h in FORECAST_HORIZONS:
        for step, (m, r) in enumerate(fc.head(h).iterrows(), start=1):
            prem = r["EARNED_PREMIUM"]
            bc, bp, bl = (band_at(bands[s], step) for s in ("CLAIMS", "PREMIUM", "LOSS_RATIO"))   # H7: by months ahead
            rows.append({
                "MONTH_DATE": m.to_timestamp(), "FORECAST_HORIZON": f"{h}M", "PERIOD_TYPE": "forecast",
                "FORECAST_CLAIMS": r["CLAIMS_COST"], "FORECAST_PREMIUM": prem,
                "CLAIMS_P10": r["CLAIMS_COST"] * bc[0], "CLAIMS_P90": r["CLAIMS_COST"] * bc[1],
                "PREMIUM_P10": prem * bp[0], "PREMIUM_P90": prem * bp[1],
                "LOSS_RATIO_P10": r["CLAIMS_COST"] / prem * bl[0] if prem > 0 else np.nan,
                "LOSS_RATIO_P90": r["CLAIMS_COST"] / prem * bl[1] if prem > 0 else np.nan,
                # B3: the month's premium split into renewals and new business (driver model), for the
                # Forecast page's plan inputs; empty when the total forecast is used
                "FORECAST_RENEWALS": prem * r["RENEWALS"] / r["WRITTEN_PREMIUM"]
                                     if "RENEWALS" in r and r["WRITTEN_PREMIUM"] > 0 else np.nan,
                "FORECAST_NEW_BUSINESS": prem * (1 - r["RENEWALS"] / r["WRITTEN_PREMIUM"])
                                         if "RENEWALS" in r and r["WRITTEN_PREMIUM"] > 0 else np.nan,
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
            "FORECAST_PREMIUM", "FORECAST_LOSS_RATIO", "CLAIMS_P10", "CLAIMS_P90", "PREMIUM_P10", "PREMIUM_P90",
            "FORECAST_CLAIM_COUNT", "FORECAST_AVG_SEVERITY", "SEASONAL_INDEX", "MODEL_TYPE",
            "MODEL_WAPE", "BIAS_CORRECTION",
            "LOSS_RATIO_P10", "LOSS_RATIO_P90",     # added 2026-11-11: last, so older report queries still read the first 17
            "FORECAST_RENEWALS", "FORECAST_NEW_BUSINESS"]   # added 2026-11-12 (plan B3)
    return out[cols]


LABEL = {"timesfm": "TimesFM", "seasonal_naive": "Seasonal naive"}
NAMES = {"EARNED_PREMIUM": "Earned premium", "CLAIM_COUNT": "Claim count", "CLAIMS_COST": "Claims cost"}


def validation_table(scores: pd.DataFrame, model: str, bands: dict, dev: dict,
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
    for series, groups in bands.items():
        for (first, last), (_, _, coverage, tail) in groups.items():
            add("band", f"Months inside the band, {first}-{last} months ahead (each origin tested on a band from the "
                f"others; tail {tail:.0%} each side, chosen for {CALIBRATION_TARGET:.0%})",
                series.replace("_", " ").capitalize(), coverage, f"{COVERAGE_RANGE[0]:.0%} to {COVERAGE_RANGE[1]:.0%}",
                COVERAGE_RANGE[0] <= coverage <= COVERAGE_RANGE[1], f"{first}-{last}")
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


def write_chart(history: pd.DataFrame, fc: pd.DataFrame, claims_band: dict) -> None:
    x_h, x_f = history.index.to_timestamp(), fc.index.to_timestamp()
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(x_h, history["REPORTED_COST"] / 1e6, color="grey", lw=1, label="Reported so far")
    ax.plot(x_h, history["CLAIMS_COST"] / 1e6, color="black", lw=1.5, label="Developed to ultimate")
    ax.plot(x_f, fc["CLAIMS_COST"] / 1e6, color="tab:red", lw=1.5, label="Forecast")
    b = np.array([band_at(claims_band, k) for k in range(1, len(fc) + 1)])
    ax.fill_between(x_f, fc["CLAIMS_COST"] * b[:, 0] / 1e6,
                    fc["CLAIMS_COST"] * b[:, 1] / 1e6, color="tab:red", alpha=0.15,
                    label="P10-P90 band (by months ahead)")
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
            for col, m in dev["method"].items():
                extra = (f", expected loss ratio {dev['elr'][col]:.1%} (Cape Cod over the latest "
                         f"{CAPE_COD_QUARTERS} accident quarters)" if m == "Bornhuetter-Ferguson" else "")
                f.write(f"- {names[col]}: {m}{extra}\n")
            f.write(f"\nChain-ladder factors at {dev['valuation_quarter']}, volume-weighted over the latest "
                    f"{DEV_DIAGONALS} diagonals (BF uses them for the share still to come). "
                    "Factor to ultimate by age of the accident quarter:\n\n")
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
    large_loss_cap = monthly.attrs.get("large_loss_cap", np.nan)
    start, end = history_window(first_premium, data_end)
    _log(f"claims data to {data_end.date()}; history {start} to {end}")

    monthly, dev = develop(monthly, data_end)
    cohorts = premium_cohorts()
    monthly["WRITTEN_PREMIUM"] = cohorts.sum(axis=1).reindex(monthly.index, fill_value=0.0)
    # Plans C2 / C3: car-years from the same policies and dates as the premium cohorts
    CAR_COHORTS["cars"] = premium_cohorts(cars=True)
    monthly["CAR_YEARS"] = CAR_COHORTS["cars"].sum().reindex(monthly.index)
    full_history = monthly.loc[start:end]

    models = (["timesfm"] if timesfm_model.available() else []) + ["seasonal_naive"]
    if "timesfm" not in models:
        _log("TimesFM not installed — seasonal naive only")
    # The models learn from the window the backtest chooses; the page still shows the report years.
    window = select_window(full_history, cohorts, models)
    history = full_history if window is None else full_history.iloc[-12 * window:]
    _log(f"training window: {training_window.label(window)} ({history.index[0]} to {history.index[-1]})")
    # Model choice, bands, card ranges and trend: the same latest origins as the window test, each
    # forecasting from its own last `window` years, exactly as the live forecast is made.
    bt = rolling_backtest(full_history, cohorts, models, None if window is None else 12 * window,
                          backtest_origins(full_history)[-WINDOW_TEST_ORIGINS:])
    scores = score(bt)
    mean_wape = scores.groupby("MODEL")["WAPE"].mean()
    for m, v in mean_wape.items():
        _log(f"validation {m}: mean WAPE {v:.1%} over {bt[bt['MODEL'] == m]['ORIGIN'].nunique()} origins")
    model = mean_wape.idxmin()
    # Plan B1: written premium = renewals of known expiries + new business, if it beats the total forecast
    drivers = premium_drivers.Drivers(list(pd.read_csv(FACTS_DIR / "fact_earned_premium.csv",
                                                       usecols=["CLASS_DESC"])["CLASS_DESC"].unique()))
    premium_method = "total"
    months = None if window is None else 12 * window
    if drivers.available:
        premium_method, method_table = select_premium_method(full_history, cohorts, model, months, bt, drivers)
        method_table.to_csv(FACTS_DIR / "fact_forecast_premium_method.csv", index=False, encoding="utf-8")
        drivers.by_channel = premium_method == "channels"
        if premium_method != "total":
            bt = rolling_backtest(full_history, cohorts, models, months,
                                  backtest_origins(full_history)[-WINDOW_TEST_ORIGINS:], drivers)
            scores = score(bt)
    else:
        _log("premium drivers: no fact_vehicle.parquet; total-premium forecast")
    split = drivers                      # kept for the new-business split even when the total forecast wins
    drivers = drivers if premium_method != "total" else None
    # Plan C4: large-loss cap on the claims rate the model learns, if it beats the raw claims
    _log(f"large-loss cap: SAR {large_loss_cap:,.0f} "
         f"per claim ({LARGE_LOSS_QUANTILE:.1%} of claim size, last {LARGE_LOSS_YEARS} years)")
    origins12 = backtest_origins(full_history)[-WINDOW_TEST_ORIGINS:]
    large_loss, ll_table = select_large_loss(full_history, cohorts, model, months, origins12, drivers, bt)
    ll_table.to_csv(FACTS_DIR / "fact_forecast_large_loss.csv", index=False, encoding="utf-8")
    if large_loss:
        bt = rolling_backtest(full_history, cohorts, models, months, origins12, drivers, True)
        scores = score(bt)
    # Plan C3: claims cost as one rate, or as how many claims x what each costs
    claims_method, cm_table = select_claims_method(full_history, cohorts, model, months, origins12, drivers,
                                                   large_loss, bt)
    cm_table.to_csv(FACTS_DIR / "fact_forecast_claims_method.csv", index=False, encoding="utf-8")
    if claims_method != "rate":
        bt = rolling_backtest(full_history, cohorts, models, months, origins12, drivers, large_loss, claims_method)
        scores = score(bt)
    b = bt[bt["MODEL"] == model]
    monthly_wape = float((b["PRED_CLAIMS_COST"] - b["ACT_CLAIMS_COST"]).abs().sum() / b["ACT_CLAIMS_COST"].sum())
    _log(f"selected: {model}")
    # Plan H6: the same choices made on earlier origins only, scored on the latest ones (honest accuracy)
    live = {"IN_SAMPLE_ERROR": float(np.mean(origin_errors(b))),
            "CONFIG": f"{training_window.label(window)} / {LABEL[model]} / {premium_method} / "
                      f"{'capped + load' if large_loss else 'raw claims'} / {CLAIMS_METHODS[claims_method]}"}
    holdout = holdout_check(full_history, cohorts, models, split, live)
    pd.DataFrame([holdout]).to_csv(FACTS_DIR / "fact_forecast_holdout.csv", index=False, encoding="utf-8")
    if holdout.get("STATUS") != "ok":
        _log(f"holdout: {holdout['STATUS']}")

    fc = forecast(model, history, cohorts, max(FORECAST_HORIZONS), drivers, large_loss, claims_method)
    if drivers is not None:
        parts = [c for c in fc.columns if c.startswith("NB_")]
        _log(f"next 12 months written premium: renewals {fc['RENEWALS'].sum() / 1e6:,.1f}M + new business "
             f"{fc['NEW_BUSINESS'].sum() / 1e6:,.1f}M"
             + (" (" + ", ".join(f"{c[3:]} {fc[c].sum() / 1e6:,.1f}M" for c in parts) + ")" if parts else ""))
    trend = trend_factor(full_history, fc, bt, model)
    pd.DataFrame([trend]).to_csv(FACTS_DIR / "fact_trend_factor.csv", index=False, encoding="utf-8")
    _log(f"trend factor {trend['TREND_FACTOR']:.3f} (forecast {trend['TREND_FACTOR_RAW']:.3f}, past bias "
         f"{trend['BIAS_PTS']:+.1f} pts; error {trend['ERROR_CORRECTED_PTS']:.1f} vs no change "
         f"{trend['ERROR_NO_CHANGE_PTS']:.1f} pts over {trend['N_ORIGINS']} origins; claims per riyal "
         f"{trend['FREQUENCY_CHANGE']:+.1%}, cost per claim {trend['SEVERITY_CHANGE']:+.1%})")
    shown_history, shown_fc = (written_month_view(full_history, fc) if year_basis() == "written_year"
                               else (full_history, fc))
    # Power BI shows only the last [report] years (as step 12), whatever the model learned from
    first_shown = current_year()[1] - int(S.config.get("report", {}).get("years", 5)) + 1
    shown_page = shown_history[shown_history.index.year >= first_shown]
    known_months = shown_page.index if year_basis() == "written_year" else shown_page.index[:0]
    # Seasonal shape of the page's months, chosen on the backtest; the 12-month totals never change
    bt_page, shape_weight = bt, 0.0
    if year_basis() == "written_year":
        shape_weight, shape_table = select_shape(bt, model, shown_history)
        shape_table.to_csv(FACTS_DIR / "fact_forecast_shape.csv", index=False, encoding="utf-8")
        bt_page = shaped_backtest(bt, model, shown_history, shape_weight)
        if shape_weight > 0:
            o = full_history.index[-1]
            prem = shape_path(shown_fc["EARNED_PREMIUM"], month_shares(shown_history["EARNED_PREMIUM"], o), shape_weight)
            claims = shape_path(shown_fc["CLAIMS_COST"], month_shares(shown_history["CLAIMS_COST"], o), shape_weight)
            ratio = np.asarray(claims) / shown_fc["CLAIMS_COST"].replace(0, np.nan).to_numpy()
            shown_fc = shown_fc.assign(EARNED_PREMIUM=np.asarray(prem), CLAIMS_COST=np.asarray(claims),
                                       CLAIM_COUNT=shown_fc["CLAIM_COUNT"].to_numpy() * np.nan_to_num(ratio, nan=1.0))
    known_share(known_months, data_end).to_csv(FACTS_DIR / "fact_forecast_known.csv", index=False, encoding="utf-8")
    scale = 1.0
    if year_basis() == "written_year" and expected_claims_basis() == "experience":
        # The page's forecast year is the current year's loss ratio times the trend factor; months keep their shape.
        recent = shown_history.iloc[-12:]
        wanted = trend["TREND_FACTOR"] * recent["CLAIMS_COST"].sum() / recent["EARNED_PREMIUM"].sum()
        head = shown_fc.head(12)
        scale = wanted / (head["CLAIMS_COST"].sum() / head["EARNED_PREMIUM"].sum())
        shown_fc = shown_fc.assign(CLAIMS_COST=shown_fc["CLAIMS_COST"] * scale, CLAIM_COUNT=shown_fc["CLAIM_COUNT"] * scale)
    ratios = band_ratios(bt_page, model, shown_history, year_basis() == "written_year", scale)
    bands = {s: band(ratios, s) for s in ("PREMIUM", "CLAIMS", "LOSS_RATIO")}
    for s, groups in bands.items():
        _log(f"{s.lower()} band by months ahead: " + "; ".join(
            f"{a}-{b} x{low:.2f} to x{high:.2f} ({cov:.0%} inside out of sample, tail {tail:.0%})"
            for (a, b), (low, high, cov, tail) in groups.items()))
    misses = total_misses(bt_page, model, shown_history, year_basis() == "written_year", scale)
    misses.to_csv(FACTS_DIR / "fact_forecast_total_miss.csv", index=False, encoding="utf-8")
    _log(f"card ranges: {misses.groupby('HORIZON')['ORIGIN'].nunique().to_dict()} origins by horizon")

    # Input check: the premium rebuilt from policy dates must equal fact_earned_premium
    premium_check = float(cohorts.sum().reindex(history.index, fill_value=0.0).sum()
                          / history["EARNED_PREMIUM"].sum())
    checks = validation_table(scores, model, bands, dev, premium_check, data_end)
    checks.to_csv(FACTS_DIR / "fact_forecast_validation.csv", index=False, encoding="utf-8")
    review = checks[checks["STATUS"] == "REVIEW"]
    _log(f"checks: {(checks['STATUS'] == 'PASS').sum()} pass, {len(review)} need review"
         + "".join(f"\n           REVIEW {r.CHECK_GROUP}: {r.MEASURE} {r.HORIZON} = {r.VALUE:.3f} ({r.THRESHOLD})"
                   for r in review.itertuples()))

    # E4: next year's new business per segment, for the optimizer's no-change book (renewals + new business)
    nb_path = FACTS_DIR / "fact_new_business.csv"
    if split.available:
        origin = full_history.index[-1]
        nb_total = (fc["NEW_BUSINESS"].head(12).sum() if "NEW_BUSINESS" in fc
                    else max(0.0, fc["WRITTEN_PREMIUM"].head(12).sum() - split.renewals(origin, fc.index[:12]).sum()))
        share = split.new_business_share(origin)
        nb = share.reset_index().assign(NB_PREMIUM=lambda d: d["NB_SHARE"] * nb_total, FORECAST_MONTHS=12,
                                        ORIGIN=str(origin))
        nb.to_csv(nb_path, index=False, encoding="utf-8")
        _log(f"new business next 12 months: {nb_total / 1e6:,.1f}M over {len(nb)} segments (by their last 12 months' share)")
    else:
        nb_path.unlink(missing_ok=True)

    out = build_rows(shown_page, shown_fc, model, monthly_wape, bands, seasonal_index(history["CLAIMS_COST"]))
    path = FACTS_DIR / "fact_forecast_seasonal.csv"
    out.to_csv(path, index=False, encoding="utf-8")
    _log(f"wrote {path} ({len(out)} rows: {len(history)} actual months, forecast {fc.index[0]} to {fc.index[-1]})")

    write_chart(history, fc, band(band_ratios(bt, model, history, False), "CLAIMS"))
    write_report(history, fc, scores, model, checks, dev, data_end)
    _log("Done.")


if __name__ == "__main__":
    main()
