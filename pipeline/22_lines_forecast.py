"""
pipeline/22_lines_forecast.py
=============================
Forecast of the lines other than motor, per class, in the motor forecast's layout (fact_forecast_seasonal.csv),
so the Forecast page shows a class's forecast when it is selected. Written-year basis like motor: a month holds the
premium of the policies written in it and those policies' claims, developed to ultimate (lines_development).

These lines have a few hundred policies and tens of claims a year, so there is no time-series model: the forecast
is a premium path times an expected loss ratio, and each choice is tested on the class's own past.

  Premium       two candidates, each forecasting the year after an origin (31 December of a usable year):
                  "same months last year"  the last usable year's months (the benchmark, as motor's seasonal naive)
                  "level x month pattern"  the last usable year's total, spread by the class's month shares over
                                           its latest PATTERN_YEARS usable years
  Claims        "credibility loss ratio": premium x the class's developed loss ratio over the [lines]
                experience_years usable years up to the origin, blended toward its department by credibility on
                the claim count (and the department toward its [lines] benchmark_loss_ratio when one is set), as
                step 21; claim count the same way. Benchmark: the last usable year's claims, month by month.
  Choice        backtest at every origin with a usable next year: error of the 3, 6 and 12-month totals, total
                absolute miss / total actual over the origins (as motor's WAPE). A method replaces the benchmark only
                if its error, averaged over the 3, 6 and 12-month horizons, is lower (the premium candidates have
                the same 12-month total; claims: the loss ratio on the chosen premium path); with fewer than MIN_ORIGINS
                origins the credibility methods are kept (they lean on the department) and marked "not tested"
  Bands         P10-P90 by horizon from the chosen methods' misses, pooled per department (all other lines when a
                department has fewer than MIN_BAND_POINTS): forecast x (1 -/+ Z_80 x the relative root mean square
                miss, sqrt(sum (actual - forecast)^2 / sum forecast^2)), the lower end at least 0. A few points' own percentiles can leave the
                forecast outside its band; this keeps it inside and widens the band when the forecast was biased
  Base          the class's last usable year (step 20); when it is before the current year (e.g. that year left
                out as incomplete) the forecast still starts after the data end and STATUS says which year it rests
                on; a class whose last usable year is older than MAX_BASE_AGE years before the current one gets no
                forecast

The backtest reads claims as developed today, so the origins' inputs are better known than they were then; the
errors are a little flattering for the claims (noted in the report).

Outputs: <facts>/fact_line_forecast.csv (motor's columns + DEPARTMENT, CLASS_DESC; SEGMENT_ID = class),
         <facts>/fact_line_forecast_method.csv (one row per class: methods, errors, base, status),
         <facts>/fact_line_forecast_backtest.csv, <reports>/lines_forecast_report.md
"""
from __future__ import annotations

import importlib

import numpy as np
import pandas as pd

from lines_development import develop
from settings import S, current_year

HORIZONS = [3, 6, 12]          # as motor's page
PATTERN_YEARS = 3              # method constants
MIN_ORIGINS = 3
MIN_BAND_POINTS = 5
MAX_BASE_AGE = 1
Z_80 = 1.2816                  # the normal's 90th percentile: P10-P90 = +/- Z_80 standard deviations
CLASS = ["DEPARTMENT", "CLASS_DESC"]
COLUMNS = ["SEGMENT_ID", "MONTH_DATE", "FORECAST_HORIZON", "PERIOD_TYPE", "FORECAST_CLAIMS", "FORECAST_PREMIUM",
           "FORECAST_LOSS_RATIO", "CLAIMS_P10", "CLAIMS_P90", "PREMIUM_P10", "PREMIUM_P90", "FORECAST_CLAIM_COUNT",
           "FORECAST_AVG_SEVERITY", "SEASONAL_INDEX", "MODEL_TYPE", "MODEL_WAPE", "BIAS_CORRECTION",
           "LOSS_RATIO_P10", "LOSS_RATIO_P90", "FORECAST_RENEWALS", "FORECAST_NEW_BUSINESS", "DEPARTMENT", "CLASS_DESC"]


def _log(m: str) -> None:
    print(f"[lines forecast] {m}", flush=True)


def monthly(p: pd.DataFrame, c: pd.DataFrame) -> pd.DataFrame:
    """Premium, developed claims and claim count by class x written month (policy effective month). A kept claim
    with no policy counts in its accident month."""
    p = p.assign(MONTH=p["POLICY_EFFECTIVE_DATE"].dt.to_period("M"))
    pm = p.drop_duplicates(["POLICY_NO", "POLICY_YEAR"])[["POLICY_NO", "POLICY_YEAR", "MONTH"]]
    c = c.merge(pm, on=["POLICY_NO", "POLICY_YEAR"], how="left")
    c["MONTH"] = c["MONTH"].fillna(c["ACC_DATE"].dt.to_period("M"))
    lost = c["MONTH"].isna().sum()
    if lost:
        _log(f"{lost} claims with neither a policy nor an accident date: not in the monthly view")
    k = CLASS + ["MONTH"]
    m = (p.groupby(k).agg(PREMIUM=("TOTAL_PREMIUM", "sum"), IBNR=("IBNR", "sum"), IBNR_COUNT=("IBNR_COUNT", "sum"))
         .join(c.dropna(subset=["MONTH"]).groupby(k).agg(RECORDED=("INCURRED", "sum"), COUNT=("CLM_NO", "size")),
               how="outer").fillna(0.0))
    m["CLAIMS"], m["CLAIM_COUNT"] = m["RECORDED"] + m["IBNR"], m["COUNT"] + m["IBNR_COUNT"]
    m = m.reset_index()
    m["YEAR"] = m["MONTH"].dt.year
    return m


def year_table(m: pd.DataFrame, used: pd.DataFrame) -> pd.DataFrame:
    """Class x year totals of the usable years only."""
    y = m.groupby(CLASS + ["YEAR"])[["PREMIUM", "CLAIMS", "COUNT", "CLAIM_COUNT"]].sum().reset_index()
    return y.merge(used, on=CLASS + ["YEAR"], how="inner")


def loss_ratios(y: pd.DataFrame, origin: int, years: int, full: float, bench: dict) -> pd.DataFrame:
    """Per class: credibility-weighted developed loss ratio and claims per riyal over its usable years in
    [origin - years + 1, origin], toward its department (and the department toward its benchmark, if set)."""
    w = y[y["YEAR"].between(origin - years + 1, origin)]
    cls = w.groupby(CLASS)[["PREMIUM", "CLAIMS", "COUNT", "CLAIM_COUNT"]].sum()
    dep = w.groupby("DEPARTMENT")[["PREMIUM", "CLAIMS", "COUNT", "CLAIM_COUNT"]].sum()
    d_lr = dep["CLAIMS"] / dep["PREMIUM"].where(dep["PREMIUM"] > 0)
    d_cr = dep["CLAIM_COUNT"] / dep["PREMIUM"].where(dep["PREMIUM"] > 0)
    d_z = np.minimum(1.0, np.sqrt(dep["COUNT"] / full))
    b = pd.Series(bench, dtype=float).reindex(dep.index)
    d_lr = pd.Series(np.where(b.notna(), d_z * d_lr + (1 - d_z) * b, d_lr), index=dep.index)
    z = np.minimum(1.0, np.sqrt(cls["COUNT"] / full))
    dl = d_lr.reindex(cls.index.get_level_values("DEPARTMENT")).to_numpy()
    dc = d_cr.reindex(cls.index.get_level_values("DEPARTMENT")).to_numpy()
    own_lr = (cls["CLAIMS"] / cls["PREMIUM"].where(cls["PREMIUM"] > 0)).to_numpy()
    own_cr = (cls["CLAIM_COUNT"] / cls["PREMIUM"].where(cls["PREMIUM"] > 0)).to_numpy()
    return pd.DataFrame({"LR": z * np.where(np.isnan(own_lr), dl, own_lr) + (1 - z) * dl,
                         "CR": z * np.where(np.isnan(own_cr), dc, own_cr) + (1 - z) * dc, "Z": z}, index=cls.index)


def paths(m: pd.DataFrame, years_used: list[int], origin: int, rates: pd.Series | None) -> dict[str, np.ndarray]:
    """12 months (January to December) after `origin` for one class, each candidate. `m` is the class's months."""
    used = [y for y in years_used if y <= origin]
    last = m[m["YEAR"] == used[-1]].set_index(m.loc[m["YEAR"] == used[-1], "MONTH"].dt.month).reindex(range(1, 13)).fillna(0.0)
    pat_years = used[-PATTERN_YEARS:]
    pat = m[m["YEAR"].isin(pat_years)].groupby(m["MONTH"].dt.month)["PREMIUM"].sum().reindex(range(1, 13)).fillna(0.0)
    shares = pat / pat.sum() if pat.sum() > 0 else pd.Series(1 / 12, index=range(1, 13))
    out = {"same months last year": last["PREMIUM"].to_numpy(),
           "level x month pattern": last["PREMIUM"].sum() * shares.to_numpy(),
           "CLAIMS last year": last["CLAIMS"].to_numpy(), "COUNT last year": last["CLAIM_COUNT"].to_numpy(),
           "SHARES": shares.to_numpy()}
    if rates is not None:
        out["LR"], out["CR"] = float(rates["LR"]), float(rates["CR"])
    return out


def err(f: np.ndarray, a: np.ndarray, h: int) -> float:
    """One origin's error of the h-month total (relative; for the backtest file only, the choice uses wape)."""
    a_, f_ = a[:h].sum(), f[:h].sum()
    return abs(f_ - a_) / abs(a_) if a_ != 0 else np.nan


def wape(b: pd.DataFrame) -> float:
    """Total absolute miss over total actual across origins: a year with almost no claims cannot dominate."""
    den = b["ACTUAL"].abs().sum()
    return float((b["FORECAST"] - b["ACTUAL"]).abs().sum() / den) if len(b) and den > 0 else np.nan


def main() -> None:
    qpath = S.facts_dir / "fact_line_quality.csv"
    out_path = S.facts_dir / "fact_line_forecast.csv"
    if not qpath.exists():
        _log("no fact_line_quality.csv (step 20 found no other-lines extract): nothing to do")
        out_path.unlink(missing_ok=True)
        return
    _, cur = current_year()
    lines, cred = S.config.get("lines", {}), S.config["credibility"]
    years, full = int(lines.get("experience_years", 5)), float(cred["full_standard_claims"])
    bench = dict(lines.get("benchmark_loss_ratio", {}))
    pricing = importlib.import_module("21_lines_pricing")
    kp, kc = pricing.kept()
    _, pol = develop(kp, kc, policies=True)
    m = monthly(pol, kc)
    q = pd.read_csv(qpath)
    used = q.loc[q["YEAR_STATUS"].isin(["used", "no claims data"]) & (q["POLICY_YEAR"] <= cur), CLASS + ["POLICY_YEAR"]].rename(
        columns={"POLICY_YEAR": "YEAR"})
    y = year_table(m, used)
    has_claims = set(map(tuple, q.loc[q["YEAR_STATUS"] != "no claims data", CLASS].drop_duplicates().to_numpy()))

    # Backtest at every origin with a usable next year
    bt = []
    for key, yrs in used.groupby(CLASS)["YEAR"]:
        yrs = sorted(yrs)
        mc = m[(m["DEPARTMENT"] == key[0]) & (m["CLASS_DESC"] == key[1])]
        for origin in yrs[:-1]:
            if origin + 1 not in yrs or len([v for v in yrs if v <= origin]) < 2:
                continue
            rates = loss_ratios(y[y["YEAR"] <= origin], origin, years, full, bench)
            r = rates.loc[key] if key in rates.index else None
            c = paths(mc, yrs, origin, r)
            act = mc[mc["YEAR"] == origin + 1].set_index(mc.loc[mc["YEAR"] == origin + 1, "MONTH"].dt.month
                                                         ).reindex(range(1, 13)).fillna(0.0)
            for h in HORIZONS:
                for meth in ("same months last year", "level x month pattern"):
                    bt.append({"DEPARTMENT": key[0], "CLASS_DESC": key[1], "ORIGIN": origin, "HORIZON": h,
                               "SERIES": "PREMIUM", "METHOD": meth, "FORECAST": c[meth][:h].sum(),
                               "ACTUAL": act["PREMIUM"].to_numpy()[:h].sum(), "ERROR": err(c[meth], act["PREMIUM"].to_numpy(), h)})
                if key in has_claims and r is not None:
                    for meth, f in (("claims last year", c["CLAIMS last year"]),
                                    ("credibility loss ratio|same months last year", c["same months last year"] * c["LR"]),
                                    ("credibility loss ratio|level x month pattern", c["level x month pattern"] * c["LR"])):
                        bt.append({"DEPARTMENT": key[0], "CLASS_DESC": key[1], "ORIGIN": origin, "HORIZON": h,
                                   "SERIES": "CLAIMS", "METHOD": meth, "FORECAST": f[:h].sum(),
                                   "ACTUAL": act["CLAIMS"].to_numpy()[:h].sum(), "ERROR": err(f, act["CLAIMS"].to_numpy(), h)})
    bt = pd.DataFrame(bt, columns=["DEPARTMENT", "CLASS_DESC", "ORIGIN", "HORIZON", "SERIES", "METHOD", "FORECAST",
                                   "ACTUAL", "ERROR"])
    bt.to_csv(S.facts_dir / "fact_line_forecast_backtest.csv", index=False, encoding="utf-8")

    # Choice per class (12-month mean error), then the live forecast from the last usable year
    rates_now = loss_ratios(y, cur, years, full, bench)
    first_month = pd.Period(f"{cur + 1}-01", "M")
    months = pd.period_range(first_month, periods=max(HORIZONS), freq="M")
    rows, meth_rows, chosen_bt = [], [], []
    for key in sorted(set(map(tuple, q[CLASS].drop_duplicates().to_numpy()))):
        yrs = sorted(used.loc[(used["DEPARTMENT"] == key[0]) & (used["CLASS_DESC"] == key[1]), "YEAR"])
        info = {"DEPARTMENT": key[0], "CLASS_DESC": key[1]}
        if not yrs or yrs[-1] < cur - MAX_BASE_AGE:
            info["STATUS"] = (f"no forecast: last usable year {yrs[-1]}, more than {MAX_BASE_AGE} year before {cur}"
                              if yrs else "no forecast: no usable year")
            meth_rows.append(info)
            continue
        base = yrs[-1]
        b = bt[(bt["DEPARTMENT"] == key[0]) & (bt["CLASS_DESC"] == key[1])]
        pick = {}
        for series, bench_m, cand in (("PREMIUM", "same months last year", "level x month pattern"),
                                      ("CLAIMS", "claims last year", "credibility loss ratio")):
            if series == "CLAIMS" and key not in has_claims:
                pick[series] = (None, "no claims data")
                continue
            tested = cand if series == "PREMIUM" else f"{cand}|{pick['PREMIUM'][0]}"
            e = b[b["SERIES"] == series]
            n = int(((e["METHOD"] == tested) & (e["HORIZON"] == 12)).sum())
            # choose on the mean over the page's horizons (the premium candidates share their 12-month total)
            score = lambda meth: np.mean([wape(e[(e["METHOD"] == meth) & (e["HORIZON"] == h)]) for h in HORIZONS])
            e12 = e[e["HORIZON"] == 12]
            e_c, e_b = wape(e12[e12["METHOD"] == tested]), wape(e12[e12["METHOD"] == bench_m])
            if n < MIN_ORIGINS:
                pick[series] = (cand, f"not tested ({n} origins)", tested)
            else:
                pick[series] = ((cand, f"{n} origins", tested) if score(tested) < score(bench_m)
                                else (bench_m, f"{n} origins", bench_m))
            info.update({f"{series}_METHOD": pick[series][0], f"{series}_TEST": pick[series][1], f"{series}_ORIGINS": n,
                         f"{series}_METHOD_ERROR_12M": e_c, f"{series}_BENCHMARK_ERROR_12M": e_b,
                         f"{series}_ERROR_12M": e_c if pick[series][0] == cand else e_b})
        mc = m[(m["DEPARTMENT"] == key[0]) & (m["CLASS_DESC"] == key[1])]
        r = rates_now.loc[key] if key in rates_now.index else None
        c = paths(mc, yrs, base, r)
        prem = c[pick["PREMIUM"][0]]
        if pick["CLAIMS"][0] == "credibility loss ratio":
            claims, count = prem * c["LR"], prem * c["CR"]
        elif pick["CLAIMS"][0] == "claims last year":
            claims, count = c["CLAIMS last year"], c["COUNT last year"]
        else:
            claims, count = np.full(12, np.nan), np.full(12, np.nan)
        info.update({"BASE_YEAR": base, "LOSS_RATIO": c.get("LR", np.nan), "CREDIBILITY_Z": float(r["Z"]) if r is not None else np.nan,
                     "PREMIUM_12M": prem.sum(), "CLAIMS_12M": np.nansum(claims) if not np.isnan(claims).all() else np.nan,
                     "STATUS": "forecast" if base == cur else f"forecast resting on {base}: {cur} not usable (step 20)"})
        meth_rows.append(info)
        for series in ("PREMIUM", "CLAIMS"):
            if pick[series][0]:
                chosen_bt.append(bt[(bt["DEPARTMENT"] == key[0]) & (bt["CLASS_DESC"] == key[1])
                                    & (bt["SERIES"] == series) & (bt["METHOD"] == pick[series][2])])
        label = f"{pick['PREMIUM'][0]} / {pick['CLAIMS'][0] or 'no claims'} (base {base})"
        for h in HORIZONS:
            for i in range(h):
                rows.append({"MONTH_DATE": months[i].to_timestamp(), "FORECAST_HORIZON": f"{h}M", "PERIOD_TYPE": "forecast",
                             "FORECAST_PREMIUM": prem[i], "FORECAST_CLAIMS": claims[i], "FORECAST_CLAIM_COUNT": count[i],
                             "SEASONAL_INDEX": c["SHARES"][months[i].month - 1] * 12, "MODEL_TYPE": label,
                             "MODEL_WAPE": info.get("PREMIUM_ERROR_12M", np.nan), "BIAS_CORRECTION": 1.0,
                             "H": h, **info})
    meth = pd.DataFrame(meth_rows)

    # Bands by horizon from the chosen methods' backtest, per department (pooled when thin)
    cb = pd.concat(chosen_bt) if chosen_bt else bt.iloc[0:0]
    cb = cb[(cb["FORECAST"] >= 0) & (cb["ACTUAL"] >= 0)]
    lr = (cb.pivot_table(index=CLASS + ["ORIGIN", "HORIZON"], columns="SERIES", values=["ACTUAL", "FORECAST"])
          if len(cb) else pd.DataFrame())
    bands = {}
    for dept in meth["DEPARTMENT"].unique():
        for h in HORIZONS:
            for series in ("PREMIUM", "CLAIMS", "LOSS_RATIO"):
                if series == "LOSS_RATIO":       # actual loss ratio on the forecast premium vs the forecast claims
                    if lr.empty or ("ACTUAL", "CLAIMS") not in lr.columns:
                        continue
                    t = lr.xs(h, level="HORIZON").dropna()
                    t = t[t[("ACTUAL", "PREMIUM")] > 0]
                    pairs = pd.DataFrame({"DEPARTMENT": t.index.get_level_values("DEPARTMENT"),
                                          "ACTUAL": (t[("ACTUAL", "CLAIMS")] / t[("ACTUAL", "PREMIUM")]
                                                     * t[("FORECAST", "PREMIUM")]).to_numpy(),
                                          "FORECAST": t[("FORECAST", "CLAIMS")].to_numpy()})
                else:
                    pairs = cb[(cb["SERIES"] == series) & (cb["HORIZON"] == h)]
                own = pairs[pairs["DEPARTMENT"] == dept]
                pool = own if len(own) >= MIN_BAND_POINTS else pairs
                if len(pool) >= 2 and (pool["FORECAST"] ** 2).sum() > 0:
                    # relative root mean square miss, pooled so each origin weighs by its size: a year forecast at
                    # almost nothing cannot blow the band up
                    rel = np.sqrt(((pool["ACTUAL"] - pool["FORECAST"]) ** 2).sum() / (pool["FORECAST"] ** 2).sum())
                    spread = Z_80 * float(rel)
                    bands[(dept, h, series)] = (max(0.0, 1 - spread), 1 + spread)
    fc = pd.DataFrame(rows)
    if len(fc):
        for series, col, (lo, hi) in (("PREMIUM", "FORECAST_PREMIUM", ("PREMIUM_P10", "PREMIUM_P90")),
                                      ("CLAIMS", "FORECAST_CLAIMS", ("CLAIMS_P10", "CLAIMS_P90"))):
            b = fc.apply(lambda r: bands.get((r["DEPARTMENT"], r["H"], series), (np.nan, np.nan)), axis=1)
            fc[lo], fc[hi] = fc[col] * b.str[0], fc[col] * b.str[1]
        b = fc.apply(lambda r: bands.get((r["DEPARTMENT"], r["H"], "LOSS_RATIO"), (np.nan, np.nan)), axis=1)
        lr_f = fc["FORECAST_CLAIMS"] / fc["FORECAST_PREMIUM"].where(fc["FORECAST_PREMIUM"] > 0)
        fc["LOSS_RATIO_P10"], fc["LOSS_RATIO_P90"] = lr_f * b.str[0], lr_f * b.str[1]

    # Actual months: the usable years of the experience window; years left out stay blank
    act = m[m["YEAR"].between(cur - years + 1, cur)].merge(used, on=CLASS + ["YEAR"], how="inner")
    act = act[act.set_index(CLASS).index.isin(meth.loc[meth["STATUS"].str.startswith("forecast"), CLASS].apply(tuple, axis=1))]
    act = pd.DataFrame({"MONTH_DATE": act["MONTH"].dt.to_timestamp(), "FORECAST_HORIZON": "ACTUAL", "PERIOD_TYPE": "actual",
                        "FORECAST_PREMIUM": act["PREMIUM"], "FORECAST_CLAIMS": act["CLAIMS"],
                        "FORECAST_CLAIM_COUNT": act["CLAIM_COUNT"], "SEASONAL_INDEX": 1.0, "MODEL_TYPE": "actual",
                        "DEPARTMENT": act["DEPARTMENT"], "CLASS_DESC": act["CLASS_DESC"]})
    no_claims = ~act.set_index(["DEPARTMENT", "CLASS_DESC"]).index.isin(list(has_claims))
    act.loc[no_claims, ["FORECAST_CLAIMS", "FORECAST_CLAIM_COUNT"]] = np.nan
    out = pd.concat([act, fc], ignore_index=True)
    if len(out):
        out["SEGMENT_ID"] = out["CLASS_DESC"]
        out["FORECAST_LOSS_RATIO"] = out["FORECAST_CLAIMS"] / out["FORECAST_PREMIUM"].where(out["FORECAST_PREMIUM"] > 0)
        out["FORECAST_AVG_SEVERITY"] = out["FORECAST_CLAIMS"] / out["FORECAST_CLAIM_COUNT"].where(out["FORECAST_CLAIM_COUNT"] > 0)
        out["MONTH_DATE"] = pd.to_datetime(out["MONTH_DATE"]).dt.strftime("%Y-%m-%d")
    out = out.reindex(columns=COLUMNS).sort_values(["DEPARTMENT", "CLASS_DESC", "FORECAST_HORIZON", "MONTH_DATE"])
    out.to_csv(out_path, index=False, encoding="utf-8")
    meth.to_csv(S.facts_dir / "fact_line_forecast_method.csv", index=False, encoding="utf-8")

    S.reports_dir.mkdir(parents=True, exist_ok=True)
    pct = lambda v: "" if pd.isna(v) else f"{v:.1%}"
    money = lambda v: "" if pd.isna(v) else f"{v / 1e6:,.2f}M"
    text = lambda v: "" if pd.isna(v) else str(v)
    with open(S.reports_dir / "lines_forecast_report.md", "w", encoding="utf-8") as f:
        f.write(f"# Other lines: forecast {months[0]} to {months[-1]}\n\nWritten-year basis; claims developed to "
                "ultimate. A method replaces the benchmark (last year's months) only if its 12-month backtest error (total miss / total "
                "actual) "
                f"is lower; with fewer than {MIN_ORIGINS} origins the credibility method is kept, not tested. The "
                "backtest reads claims as developed today, so its claims errors are a little flattering.\n\n"
                "| Department | Class | Status | Premium method | Error 12M (benchmark) | Claims method | Error 12M "
                "(benchmark) | Loss ratio | Premium 12M | Claims 12M |\n|---|---|---|---|---:|---|---:|---:|---:|---:|\n")
        for r in meth.to_dict("records"):
            g = lambda a: r.get(a, np.nan)
            f.write(f"| {r['DEPARTMENT']} | {r['CLASS_DESC']} | {r['STATUS']} | {text(g('PREMIUM_METHOD'))} "
                    f"({text(g('PREMIUM_TEST'))}) | {pct(g('PREMIUM_ERROR_12M'))} ({pct(g('PREMIUM_BENCHMARK_ERROR_12M'))}) | "
                    f"{text(g('CLAIMS_METHOD'))} ({text(g('CLAIMS_TEST'))}) | {pct(g('CLAIMS_ERROR_12M'))} "
                    f"({pct(g('CLAIMS_BENCHMARK_ERROR_12M'))}) | {pct(g('LOSS_RATIO'))} | {money(g('PREMIUM_12M'))} | "
                    f"{money(g('CLAIMS_12M'))} |\n")
    _log(f"{len(meth)} classes; forecast {months[0]} to {months[-1]}; {len(bt):,} backtest rows; {len(bands)} bands")
    for r in meth.to_dict("records"):
        g = lambda a: r.get(a, np.nan)
        if not str(r["STATUS"]).startswith("forecast"):
            _log(f"  {r['DEPARTMENT']} / {r['CLASS_DESC']}: {r['STATUS']}")
            continue
        _log(f"  {r['DEPARTMENT']} / {r['CLASS_DESC']}: premium {money(g('PREMIUM_12M'))} by {text(g('PREMIUM_METHOD'))} "
             f"({text(g('PREMIUM_TEST'))}; 12M error {pct(g('PREMIUM_ERROR_12M'))} vs {pct(g('PREMIUM_BENCHMARK_ERROR_12M'))}); "
             f"claims {money(g('CLAIMS_12M'))} by {text(g('CLAIMS_METHOD'))} ({text(g('CLAIMS_TEST'))}; "
             f"{pct(g('CLAIMS_ERROR_12M'))} vs {pct(g('CLAIMS_BENCHMARK_ERROR_12M'))}); LR {pct(g('LOSS_RATIO'))}; {r['STATUS']}")


if __name__ == "__main__":
    main()
