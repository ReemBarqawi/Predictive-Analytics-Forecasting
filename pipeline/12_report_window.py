"""
12_report_window.py — the policy years the dashboard shows.

The models use the full history. Power BI loads only the latest [report] years policy years
(config.toml), ending at the current year, plus the forecast months. This step works out those
years from the data and writes them for Power BI to filter on.

Current year = the last calendar year the claims reach to its end (claims to 18 Jan 2025 -> 2024).
Forecast year = the year after it. Its months that the data already covers in full are actuals,
the rest come from the forecast (claims to 18 Jan 2025 -> 2025, 0 actual months; claims to
30 Jun 2025 -> still 2025, 5 actual months). When the claims reach 31 Dec 2025 both roll on a year.
Every page reads these two years from here, so they cannot drift apart.
History years = every policy year the pipeline's all-years tables cover, up to the year of the
data cut-off (policy years after it are data errors).

The two large tables are also written as report copies holding only those years, so Power BI's
refresh reads a small file instead of the full one. The policy copy keeps only the columns the
report uses (it only counts policies per segment); the claim copy keeps every column.

Reads : 02_facts/fact_claim.csv, 02_facts/fact_segment_performance_by_year.csv,
        02_facts/fact_policy_pricing.csv
Writes: 03_dims/dim_report_window.csv   one row:
        DATA_CUTOFF, CURRENT_YEAR, FIRST_YEAR, REPORT_YEARS, HISTORY_FIRST_YEAR, HISTORY_LAST_YEAR,
        FORECAST_YEAR, FORECAST_ACTUAL_MONTHS
        02_facts/fact_policy_pricing_report.csv, 02_facts/fact_claim_report.csv
"""
import sys

import pandas as pd

from settings import S, current_year, year_basis, expected_claims_basis

# Columns of fact_policy_pricing the dashboard uses: the segment keys of its relationships,
# the year and the cancelled flag (measure "BM Total Policies").
POLICY_REPORT_COLUMNS = ["POLICY_YEAR", "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC", "IS_CANCELLED"]

years = int(S.config.get("report", {}).get("years", 5))
if years < 1:
    sys.exit("[report] years in config.toml must be 1 or more.")

cutoff, current = current_year()
trend_path = S.facts_dir / "fact_trend_factor.csv"

seg = pd.read_csv(S.facts_dir / "fact_segment_performance_by_year.csv", usecols=["POLICY_YEAR"])
history = seg["POLICY_YEAR"][seg["POLICY_YEAR"] <= cutoff.year]
history_first, history_last = int(history.min()), int(history.max())
first = max(current - years + 1, history_first)

later = sorted(int(y) for y in seg["POLICY_YEAR"][seg["POLICY_YEAR"] > cutoff.year].unique())
if later:
    print(f"  WARNING: policy years after the data cut-off {later}: check the extract")

forecast = current + 1
# months of the forecast year the data covers in full (the cut-off month counts only at its last day)
full_month = cutoff.month if cutoff.is_month_end else cutoff.month - 1
forecast_actual_months = full_month if cutoff.year == forecast else 0

out = pd.DataFrame([{
    "DATA_CUTOFF": cutoff.date().isoformat(),
    "CURRENT_YEAR": current,
    "FIRST_YEAR": first,
    "REPORT_YEARS": current - first + 1,
    "HISTORY_FIRST_YEAR": history_first,
    "HISTORY_LAST_YEAR": history_last,
    "FORECAST_YEAR": forecast,
    "FORECAST_ACTUAL_MONTHS": forecast_actual_months,
    "YEAR_BASIS": year_basis(),
    "YEAR_BASIS_STATUS": S.config.get("report", {}).get("year_basis_status", ""),
    "METHOD_REVIEW_STATUS": S.config.get("report", {}).get("method_review_status", ""),
    "EXPECTED_CLAIMS": expected_claims_basis(),
    "TREND_FACTOR": float(pd.read_csv(trend_path)["TREND_FACTOR"].iloc[0]) if trend_path.exists() else 1.0,
    # Settings the report's measures read, so no threshold is typed in DAX
    "TARGET_COMBINED_RATIO": S.config["business"]["target_combined_ratio"],
    "FULL_STANDARD_CLAIMS": S.config["credibility"]["full_standard_claims"],
    "RELIABILITY_HIGH": S.config["credibility"]["reliability_high"],
    "RELIABILITY_LOW": S.config["credibility"]["reliability_low"],
    "REPRICE_CAP": S.config["levers"]["reprice_cap"],
    "UW_SEVERITY_REDUCTION": S.config["levers"]["uw_severity_reduction"],
    "GLIDE_UW_SHARE_MONTH_9": S.config["levers"]["glide_uw_share_month_9"],
    "ANNUAL_GROWTH_DEFAULT": S.config["forecast"]["annual_growth_default"],
    **{key.upper(): value for key, value in S.config["report_tiers"].items()},
}])
path = S.dims_dir / "dim_report_window.csv"
out.to_csv(path, index=False, encoding="utf-8")
print(f"  claims to {cutoff.date()}: current year {current}; dashboard shows {first}-{current} "
      f"(history {history_first}-{history_last}); forecast year {forecast} "
      f"({forecast_actual_months} months actual)")
print(f"Wrote {path}")


def in_window(df: pd.DataFrame) -> pd.DataFrame:
    return df[pd.to_numeric(df["POLICY_YEAR"], errors="coerce").between(first, current)]


# Read as text so every value is written back exactly as the earlier steps wrote it.
text = dict(dtype=str, keep_default_na=False)
policies = pd.concat(in_window(chunk) for chunk in pd.read_csv(
    S.facts_dir / "fact_policy_pricing.csv", usecols=POLICY_REPORT_COLUMNS, chunksize=500_000, **text))
claims_all = pd.read_csv(S.facts_dir / "fact_claim.csv", **text)
claims_report = in_window(claims_all)
if year_basis() == "written_year":
    # The claim-detail cards sit beside the current-year levers: same policies.
    policies = policies[policies["POLICY_YEAR"] == str(current)]
    claims_report = claims_report[claims_report["POLICY_YEAR"] == str(current)]
for name, df in (("fact_policy_pricing_report", policies[POLICY_REPORT_COLUMNS]),
                 ("fact_claim_report", claims_report)):
    out_path = S.facts_dir / f"{name}.csv"
    df.to_csv(out_path, index=False, encoding="utf-8")
    print(f"Wrote {len(df):,} rows, {out_path.stat().st_size / 1e6:,.0f} MB -> {out_path}")
