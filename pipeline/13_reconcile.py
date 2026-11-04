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
trend = float(pd.read_csv(F / "fact_trend_factor.csv")["TREND_FACTOR"].iloc[0]) if experience and (F / "fact_trend_factor.csv").exists() else 1.0
optimal = pd.read_csv(F / "fact_optimal_scenarios.csv")
base = optimal[optimal["sensitivity_case"] == "base"].set_index(SEG)
check_segments("optimizer", "Today premium", base["nochange_premium"] / renewal, now["EARNED_PREMIUM"])
check_segments("optimizer", "Today claims", base["nochange_claims"] / (renewal * trend), now["ULTIMATE_CLAIMS"])
check_segments("optimizer", "Today expenses", base["nochange_expenses"] / renewal, now["COMMISSION_SUM"] + now["ADMIN_EXPENSE"])
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
if written_year:
    for year, g in actual.groupby(actual["MONTH_DATE"].dt.year):
        if len(g) == 12 and year in report_years:
            row = by_year[by_year["POLICY_YEAR"] == year]
            check("forecast", f"{year}: 12 months of premium = segment results", g["FORECAST_PREMIUM"].sum(), row["EARNED_PREMIUM"].sum())
            check("forecast", f"{year}: 12 months of claims = segment results", g["FORECAST_CLAIMS"].sum(), row["ULTIMATE_CLAIMS"].sum())
    last = actual["MONTH_DATE"].max()
    if experience and last.year == current and last.month == 12:
        recent = actual[actual["MONTH_DATE"].dt.year == current]
        wanted = trend * recent["FORECAST_CLAIMS"].sum() / recent["FORECAST_PREMIUM"].sum()
        have = ahead["FORECAST_CLAIMS"].sum() / ahead["FORECAST_PREMIUM"].sum()
        check("forecast", "Next-year loss ratio x 1M = current x trend", have * 1e6, wanted * 1e6)

# ---- alerts: results so far on the same policies, and the estimate built from them
if written_year:
    inside = earned[(earned["POLICY_YEAR"] == current) & (earned["MONTH_DATE"].dt.year == current)]
    check("alerts", "In-year premium = earned table", alerts["EP_L12"].sum(), inside["EARNED_PREMIUM"].sum())
    reliable = F / "fact_expected_claims.csv"
    if experience and reliable.exists():
        lr = pd.read_csv(reliable).set_index(SEG)["RELIABLE_LR"]
        check_segments("alerts", "Segment claims = whole premium x reliable loss ratio", now["ULTIMATE_CLAIMS"], (now["EARNED_PREMIUM"] * lr).dropna())

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

table = pd.DataFrame(results)
table.to_csv(F / "fact_reconciliation.csv", index=False, encoding="utf-8")
failed = table[table["STATUS"] == "FAIL"]
print(f"  {len(table) - len(failed)} of {len(table)} checks pass; details in {F / 'fact_reconciliation.csv'}")
for r in failed.itertuples():
    print(f"  FAIL [{r.CHECK_GROUP}] {r.CHECK}: {r.VALUE_A:,.2f} vs {r.VALUE_B:,.2f} (difference {r.DIFFERENCE:,.2f}, allowed {r.TOLERANCE:,.2f}) {r.NOTE}")
if len(failed):
    sys.exit(f"Reconciliation failed: {len(failed)} check(s). Do not refresh the report from this run.")
