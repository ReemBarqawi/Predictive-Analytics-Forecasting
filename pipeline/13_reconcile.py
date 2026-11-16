"""
13_reconcile.py — the gate: stop the run when the numbers disagree (docs/METRIC_STANDARD.md §7).

Compares the tables Power BI loads with each other and with their sources. Every check is written
to 02_facts/fact_reconciliation.csv; any failure makes this step, and so the run, fail. Do not refresh
the report from a failed run.

Two amounts agree when they differ by no more than [reconcile] tolerance_sar, or tolerance_share of
the amount (the optimizer works from policy records, so it differs from the earned table by a few riyals).
"""
import sys

import pandas as pd

from settings import S, current_year, expected_claims_basis, year_basis

SEG = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]
TOL_SAR = S.config["reconcile"]["tolerance_sar"]
TOL_SHARE = S.config["reconcile"]["tolerance_share"]
results = []


def check(group, name, a, b, note=""):
    """a and b must agree within tolerance."""
    tolerance = max(TOL_SAR, TOL_SHARE * abs(b))
    results.append({"CHECK_GROUP": group, "CHECK": name, "VALUE_A": a, "VALUE_B": b, "DIFFERENCE": a - b,
                    "TOLERANCE": tolerance, "STATUS": "PASS" if abs(a - b) <= tolerance else "FAIL", "NOTE": note})


def check_true(group, name, ok, note=""):
    results.append({"CHECK_GROUP": group, "CHECK": name, "VALUE_A": float(bool(ok)), "VALUE_B": 1.0, "DIFFERENCE": 0.0 if ok else 1.0,
                    "TOLERANCE": 0.0, "STATUS": "PASS" if ok else "FAIL", "NOTE": note})


def check_segments(group, name, a, b):
    """Two per-segment series must cover the same segments and agree on each; reports the worst."""
    both = pd.concat([a.rename("A"), b.rename("B")], axis=1).fillna(0.0)
    if both.empty:                       # no segment on either side (e.g. a year with no business): they agree
        check_true(group, f"{name}: no segments on either side", True, "nothing to compare")
        return
    both["DIFF"] = (both["A"] - both["B"]).abs()
    worst = both["DIFF"].idxmax()
    check(group, f"{name}: worst segment", both.at[worst, "A"], both.at[worst, "B"], " | ".join(map(str, worst)))
    check(group, f"{name}: total", both["A"].sum(), both["B"].sum())


F = S.facts_dir
by_year = pd.read_csv(F / "fact_segment_performance_by_year.csv")
earned = pd.read_csv(F / "fact_earned_premium.csv", usecols=SEG + ["POLICY_YEAR", "MONTH_DATE", "EARNED_PREMIUM"], parse_dates=["MONTH_DATE"])
window = pd.read_csv(S.dims_dir / "dim_report_window.csv").iloc[0]
cutoff, current = current_year()
now = by_year[by_year["POLICY_YEAR"] == current].groupby(SEG).sum(numeric_only=True)
now = now[now["EARNED_PREMIUM"] > 0]
written_year = year_basis() == "written_year"
experience = expected_claims_basis() == "experience"

# ---- calendar: every step on the same year
run = pd.read_csv(F / "fact_optimizer_run.csv").iloc[0]
alerts = pd.read_csv(F / "fact_alert_basis.csv", parse_dates=["WINDOW_END"])
check_true("calendar", "Report window has the current year", int(window["CURRENT_YEAR"]) == current, f"data to {cutoff.date()}, current {current}")
check_true("calendar", "Forecast year follows it", int(window["FORECAST_YEAR"]) == current + 1)
check_true("calendar", "Optimizer prices the current year", int(run["policy_year"]) == current, f"optimizer {int(run['policy_year'])}")
check_true("calendar", "Target is the configured one", abs(float(run["target_combined_ratio"]) - S.config["business"]["target_combined_ratio"]) < 1e-9)

# ---- premium: segment results vs the earned table
for year, value in by_year.groupby("POLICY_YEAR")["EARNED_PREMIUM"].sum().items():
    check("premium", f"Policy year {year}: segment results = earned table", value, earned.loc[earned["POLICY_YEAR"] == year, "EARNED_PREMIUM"].sum())

# ---- claims
claims = pd.read_csv(F / "fact_claim.csv", usecols=["POLICY_YEAR", "ULTIMATE_CLAIM", "POLICY_MATCHED", "TOTAL_PAID"])
matched = claims[claims["POLICY_MATCHED"].astype(str).str.lower() == "true"]
report_years = range(int(window["FIRST_YEAR"]), current + 1)
for year in report_years:
    row = by_year[by_year["POLICY_YEAR"] == year]
    reported = row["ACTUAL_ULTIMATE_CLAIMS"].sum()
    check("claims", f"Policy year {year}: reported = claim table", reported, matched.loc[matched["POLICY_YEAR"] == year, "ULTIMATE_CLAIM"].sum())
    check_true("claims", f"Policy year {year}: ultimate is not below reported", row["ULTIMATE_CLAIMS"].sum() >= reported - TOL_SAR,
               f"ultimate {row['ULTIMATE_CLAIMS'].sum():,.0f}, reported {reported:,.0f}")
    result = row["EARNED_PREMIUM"].sum() - row["ULTIMATE_CLAIMS"].sum() - row["COMMISSION_SUM"].sum() - row["ADMIN_EXPENSE"].sum()
    check("claims", f"Policy year {year}: result = premium - claims - expenses", row["UW_PROFIT"].sum(), result)

# ---- zones
zones = pd.read_csv(S.dims_dir / "dim_zone.csv")
check_true("zones", "Every segment's zone is in dim_zone", set(by_year["ZONE"]) <= set(zones["ZONE"]), str(sorted(set(by_year["ZONE"]) - set(zones["ZONE"]))))
check_true("zones", "No office without a location", zones["LATITUDE"].notna().all() and zones["ZONE"].notna().all())

# ---- levers: same book as segment results
levers = pd.read_csv(F / "fact_lever_allocation.csv").set_index(SEG)
check_segments("levers", "Premium", levers["baseline_earned_premium"], now["EARNED_PREMIUM"])
check_segments("levers", "Claims", levers["baseline_ultimate_claims"], now["ULTIMATE_CLAIMS"])

# ---- optimizer: its today book = segment results
renewal = S.config["optimizer"]["baseline_renewal"]
rates_path = F / "fact_renewal_rates.csv"
if rates_path.exists():       # E1: each segment's measured renewal rate (step 17), as the optimizer uses it
    rates = pd.read_csv(rates_path)
    renewal = rates.set_index(SEG)["RENEWAL_RATE"].reindex(now.index).fillna(float(rates["PORTFOLIO_RATE"].iloc[0]))
trend = float(pd.read_csv(F / "fact_trend_factor.csv")["TREND_FACTOR"].iloc[0]) if experience and (F / "fact_trend_factor.csv").exists() else 1.0
optimal = pd.read_csv(F / "fact_optimal_scenarios.csv")
base = optimal[optimal["sensitivity_case"] == "base"].set_index(SEG)
# E4: the no-change book = renewals + new business. The renewal part is today's book x renewal (x trend for
# claims); the new-business part is the forecast's, split by segment (step 07)
ren = "nochange_renewal_" if "nochange_renewal_premium" in base else "nochange_"
check_segments("optimizer", "Today premium", base[f"{ren}premium"] / renewal, now["EARNED_PREMIUM"])
check_segments("optimizer", "Today claims", base[f"{ren}claims"] / (renewal * trend), now["ULTIMATE_CLAIMS"])
check_segments("optimizer", "Today expenses", base[f"{ren}expenses"] / renewal, now["COMMISSION_SUM"] + now["ADMIN_EXPENSE"])
if "nochange_nb_premium" in base:
    check("optimizer", "No-change book = renewals + new business",
          base["nochange_premium"].sum(), (base["nochange_renewal_premium"] + base["nochange_nb_premium"]).sum())
    nb_path = F / "fact_new_business.csv"
    if nb_path.exists():
        nb = pd.read_csv(nb_path).set_index(SEG)["NB_PREMIUM"]
        priced = nb.reindex(base.index).fillna(0.0)
        check("optimizer", "New business at no change = the forecast's, on the priced segments",
              base["nochange_nb_premium"].sum(), priced.sum())
scenarios = pd.read_csv(F / "fact_scenario_results.csv", usecols=["segment_id", "sensitivity_case", "premium_change", "scenario_premium"])
scenarios["PRICE"] = scenarios["premium_change"].round(4)
picked = optimal.assign(PRICE=optimal["optimal_premium_change"].round(4)).merge(scenarios, on=["segment_id", "sensitivity_case", "PRICE"], how="left")
check_true("optimizer", "Every recommended price is a calculated scenario", picked["scenario_premium"].notna().all())
check("optimizer", "Recommended premium = its scenario", picked["optimal_premium"].sum(), picked["scenario_premium"].sum())

# ---- forecast page
forecast = pd.read_csv(F / "fact_forecast_seasonal.csv", parse_dates=["MONTH_DATE"])
actual = forecast[forecast["PERIOD_TYPE"] == "actual"]
ahead = forecast[(forecast["PERIOD_TYPE"] == "forecast") & (forecast["FORECAST_HORIZON"] == "12M")]
check_true("forecast", "Forecast has 12 months", len(ahead) == 12, f"{len(ahead)} months")
# Cards = chart: each horizon's card is the first months of the same 12-month line
line = ahead.sort_values("MONTH_DATE")
for h in ("3M", "6M"):
    card = forecast[(forecast["PERIOD_TYPE"] == "forecast") & (forecast["FORECAST_HORIZON"] == h)]
    n = int(h[:-1])
    for col, name in (("FORECAST_PREMIUM", "premium"), ("FORECAST_CLAIMS", "claims")):
        check("forecast", f"{h} card {name} = first {n} months of the chart", card[col].sum(), line[col].head(n).sum())
# Every forecast month sits inside its own P10-P90 band
for value, low, high in (("FORECAST_PREMIUM", "PREMIUM_P10", "PREMIUM_P90"), ("FORECAST_CLAIMS", "CLAIMS_P10", "CLAIMS_P90"),
                         ("FORECAST_LOSS_RATIO", "LOSS_RATIO_P10", "LOSS_RATIO_P90")):
    if low in ahead:
        inside = ahead[value].between(ahead[low] - 1e-9, ahead[high] + 1e-9)
        check_true("forecast", f"Forecast {value.split('_', 1)[1].lower().replace('_', ' ')} inside its band every month",
                   inside.all(), f"{int((~inside).sum())} months outside")
if written_year:
    known = pd.read_csv(F / "fact_forecast_known.csv", parse_dates=["MONTH_DATE"])
    for year, g in actual.groupby(actual["MONTH_DATE"].dt.year):
        if len(g) == 12 and year in report_years:
            row = by_year[by_year["POLICY_YEAR"] == year]
            check("forecast", f"{year}: 12 months of premium = segment results", g["FORECAST_PREMIUM"].sum(), row["EARNED_PREMIUM"].sum())
            check("forecast", f"{year}: 12 months of claims = segment results", g["FORECAST_CLAIMS"].sum(), row["ULTIMATE_CLAIMS"].sum())
            check("forecast", f"{year}: 12 months of recorded claims = reported claims", known.loc[known["MONTH_DATE"].dt.year == year, "RECORDED_CLAIMS"].sum(), row["ACTUAL_ULTIMATE_CLAIMS"].sum())
    months = actual.merge(known, on="MONTH_DATE")
    below = months[months["FORECAST_CLAIMS"] < months["RECORDED_CLAIMS"] - TOL_SAR]
    check_true("forecast", "No month's claims sit below its recorded claims", below.empty, f"{len(below)} months below")
    last = actual["MONTH_DATE"].max()
    if experience and last.year == current and last.month == 12:
        recent = actual[actual["MONTH_DATE"].dt.year == current]
        wanted = trend * recent["FORECAST_CLAIMS"].sum() / recent["FORECAST_PREMIUM"].sum()
        have = ahead["FORECAST_CLAIMS"].sum() / ahead["FORECAST_PREMIUM"].sum()
        check("forecast", "Next-year loss ratio x 1M = current x trend", have * 1e6, wanted * 1e6)

# ---- alerts: results so far on the same policies, and the estimate built from them
if written_year and len(alerts):
    # Over the alerts' own 12 months (step 11 drops a last month whose claims are not yet fully reported), each
    # policy year's premium earned inside that year, as the alerts count it
    w0, w1 = pd.Timestamp(alerts["WINDOW_START"].iloc[0]), pd.Timestamp(alerts["WINDOW_END"].iloc[0])
    inside = earned[earned["MONTH_DATE"].between(w0, w1) & (earned["MONTH_DATE"].dt.year == earned["POLICY_YEAR"])]
    check("alerts", "In-year premium = earned table (alerts' window)", alerts["EP_L12"].sum(),
          inside["EARNED_PREMIUM"].sum(), str(alerts["WINDOW_LABEL"].iloc[0]))

# ---- reserving: the one source of ultimate claims (step 14)
reserve = pd.read_csv(F / "fact_claims_ultimate.csv")
check("reserving", "Reported + still to come = ultimate (all years)",
      (reserve["REPORTED_CLAIMS"] + reserve["STILL_TO_COME"]).sum(), reserve["ULTIMATE_CLAIMS"].sum())
check_true("reserving", "Ultimate is never below reported", (reserve["STILL_TO_COME"] >= -TOL_SAR).all())
for year in report_years:
    r_year = reserve[reserve["POLICY_YEAR"] == year].groupby(SEG)["ULTIMATE_CLAIMS"].sum()
    s_year = by_year[by_year["POLICY_YEAR"] == year].groupby(SEG)["ULTIMATE_CLAIMS"].sum()
    check_segments("reserving", f"Policy year {year}: segment claims = reserving", s_year[s_year != 0], r_year[r_year != 0])

# ---- report copies Power BI loads
claim_copy = pd.read_csv(F / "fact_claim_report.csv", usecols=["POLICY_YEAR", "ULTIMATE_CLAIM", "POLICY_MATCHED"])
if written_year:
    copy_now = claim_copy[claim_copy["POLICY_MATCHED"].astype(str).str.lower() == "true"]["ULTIMATE_CLAIM"].sum()
    check("report copies", "Claim copy = reported claims of the current year", copy_now, now["ACTUAL_ULTIMATE_CLAIMS"].sum())

# ---- bridge to finance's calendar-year premium
own = earned[earned["POLICY_YEAR"] == current]
calendar = earned[earned["MONTH_DATE"].dt.year == current]["EARNED_PREMIUM"].sum()
outside = own[own["MONTH_DATE"].dt.year != current]["EARNED_PREMIUM"].sum()
earlier = earned[(earned["POLICY_YEAR"] != current) & (earned["MONTH_DATE"].dt.year == current)]["EARNED_PREMIUM"].sum()
check("bridge", "Written - earned outside the year + other policies' earned in it = calendar earned",
      own["EARNED_PREMIUM"].sum() - outside + earlier, calendar, f"{own['EARNED_PREMIUM'].sum():,.0f} - {outside:,.0f} + {earlier:,.0f}")

# ---- other lines (steps 20-21), only when their extract exists: everything extracted is either used or left
# out with a reason, pricing uses only what was kept, and a line without claims has no loss ratio or price
if (F / "fact_line_quality.csv").exists():
    lq = pd.read_csv(F / "fact_line_quality.csv")
    lp = pd.read_parquet(F / "fact_line_policy.parquet", columns=["TOTAL_PREMIUM", "EXCLUDE_REASON"])
    lc = pd.read_parquet(F / "fact_line_claim.parquet", columns=["INCURRED", "EXCLUDE_REASON"])
    raw_p = pd.read_parquet(S.raw_dir / "lines_policies.parquet", columns=["TOTAL_PREMIUM"])
    raw_c = pd.read_parquet(S.raw_dir / "lines_claims.parquet", columns=["CLM_NO"])
    check("other lines", "Premium extracted = quality table", float(raw_p["TOTAL_PREMIUM"].sum()), lq["PREMIUM"].sum())
    check("other lines", "Claims extracted = quality table", float(len(raw_c)), lq["CLAIMS"].sum())
    check("other lines", "Premium kept = quality table's used premium",
          lp.loc[lp["EXCLUDE_REASON"] == "", "TOTAL_PREMIUM"].sum(), lq["PREMIUM_USED"].sum())
    check("other lines", "Incurred kept = quality table's used incurred",
          lc.loc[lc["EXCLUDE_REASON"] == "", "INCURRED"].sum(), lq["INCURRED_USED"].sum())
    if (F / "fact_line_pricing.csv").exists():
        lpr = pd.read_csv(F / "fact_line_pricing.csv")
        lex = pd.read_csv(F / "fact_line_experience.csv")
        check("other lines", "Premium priced = used experience years' premium", lpr["PREMIUM"].sum(),
              lex.loc[lex["USED"], "PREMIUM_USED"].sum())
        check_true("other lines", "Every class extracted has a pricing row",
                   set(map(tuple, lq[["DEPARTMENT", "CLASS_DESC"]].drop_duplicates().to_numpy()))
                   == set(map(tuple, lpr[["DEPARTMENT", "CLASS_DESC"]].to_numpy())))
        check_true("other lines", "Credibility between 0 and 1", lpr["CREDIBILITY_Z"].dropna().between(0, 1).all())
        if (F / "fact_line_segment_performance_by_year.csv").exists():
            lseg = pd.read_csv(F / "fact_line_segment_performance_by_year.csv")
            check("other lines", "Segment results premium = quality table's used premium",
                  lseg["EARNED_PREMIUM"].sum(), lq["PREMIUM_USED"].sum())
            check("other lines", "Segment results claims = quality table's used incurred",
                  lseg["ACTUAL_ULTIMATE_CLAIMS"].sum(), lq["INCURRED_USED"].sum())
        none = lpr[lpr["STATUS"] == "no claims data"]
        check_true("other lines", "No claims data: no loss ratio or price",
                   none[["LOSS_RATIO", "INDICATED_CHANGE"]].isna().all().all(), f"{len(none)} class(es)")

table = pd.DataFrame(results)
table.to_csv(F / "fact_reconciliation.csv", index=False, encoding="utf-8")
failed = table[table["STATUS"] == "FAIL"]
print(f"  {len(table) - len(failed)} of {len(table)} checks pass; details in {F / 'fact_reconciliation.csv'}")
for r in failed.itertuples():
    print(f"  FAIL [{r.CHECK_GROUP}] {r.CHECK}: {r.VALUE_A:,.2f} vs {r.VALUE_B:,.2f} (difference {r.DIFFERENCE:,.2f}, allowed {r.TOLERANCE:,.2f}) {r.NOTE}")
if len(failed):
    sys.exit(f"Reconciliation failed: {len(failed)} check(s). Do not refresh the report from this run.")
