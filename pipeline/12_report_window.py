"""
12_report_window.py — the policy years the dashboard shows.

The models use the full history. Power BI loads only the latest [report] years policy years
(config.toml), ending at the current year, plus the forecast months. This step works out those
years from the data and writes them for Power BI to filter on.

Current year = the last calendar year the claims reach to its end (claims to 18 Jan 2025 -> 2024).
History years = every policy year the pipeline's all-years tables cover, up to the year of the
data cut-off (policy years after it are data errors).

The two large tables are also written as report copies holding only those years, so Power BI's
refresh reads a small file instead of the full one. The policy copy keeps only the columns the
report uses (it only counts policies per segment); the claim copy keeps every column.

Reads : 02_facts/fact_claim.csv, 02_facts/fact_segment_performance_by_year.csv,
        02_facts/fact_policy_pricing.csv
Writes: 03_dims/dim_report_window.csv   one row:
        DATA_CUTOFF, CURRENT_YEAR, FIRST_YEAR, REPORT_YEARS, HISTORY_FIRST_YEAR, HISTORY_LAST_YEAR
        02_facts/fact_policy_pricing_report.csv, 02_facts/fact_claim_report.csv
"""
import sys

import pandas as pd

from settings import S

# Columns of fact_policy_pricing the dashboard uses: the segment keys of its relationships,
# the year and the cancelled flag (measure "BM Total Policies").
POLICY_REPORT_COLUMNS = ["POLICY_YEAR", "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC", "IS_CANCELLED"]

years = int(S.config.get("report", {}).get("years", 5))
if years < 1:
    sys.exit("[report] years in config.toml must be 1 or more.")

claims = pd.read_csv(S.facts_dir / "fact_claim.csv", usecols=["ACC_DATE"], parse_dates=["ACC_DATE"])
cutoff = claims["ACC_DATE"].max()
if pd.isna(cutoff):
    sys.exit("fact_claim.csv has no accident dates: cannot tell which year is current.")
current = cutoff.year if (cutoff.month, cutoff.day) == (12, 31) else cutoff.year - 1

seg = pd.read_csv(S.facts_dir / "fact_segment_performance_by_year.csv", usecols=["POLICY_YEAR"])
history = seg["POLICY_YEAR"][seg["POLICY_YEAR"] <= cutoff.year]
history_first, history_last = int(history.min()), int(history.max())
first = max(current - years + 1, history_first)

later = sorted(int(y) for y in seg["POLICY_YEAR"][seg["POLICY_YEAR"] > cutoff.year].unique())
if later:
    print(f"  WARNING: policy years after the data cut-off {later}: check the extract")

out = pd.DataFrame([{
    "DATA_CUTOFF": cutoff.date().isoformat(),
    "CURRENT_YEAR": current,
    "FIRST_YEAR": first,
    "REPORT_YEARS": current - first + 1,
    "HISTORY_FIRST_YEAR": history_first,
    "HISTORY_LAST_YEAR": history_last,
}])
path = S.dims_dir / "dim_report_window.csv"
out.to_csv(path, index=False, encoding="utf-8")
print(f"  claims to {cutoff.date()}: current year {current}; dashboard shows {first}-{current} "
      f"(history {history_first}-{history_last})")
print(f"Wrote {path}")


def in_window(df: pd.DataFrame) -> pd.DataFrame:
    return df[pd.to_numeric(df["POLICY_YEAR"], errors="coerce").between(first, current)]


# Read as text so every value is written back exactly as the earlier steps wrote it.
text = dict(dtype=str, keep_default_na=False)
policies = pd.concat(in_window(chunk) for chunk in pd.read_csv(
    S.facts_dir / "fact_policy_pricing.csv", usecols=POLICY_REPORT_COLUMNS, chunksize=500_000, **text))
claims_all = pd.read_csv(S.facts_dir / "fact_claim.csv", **text)
for name, df in (("fact_policy_pricing_report", policies[POLICY_REPORT_COLUMNS]),
                 ("fact_claim_report", in_window(claims_all))):
    out_path = S.facts_dir / f"{name}.csv"
    df.to_csv(out_path, index=False, encoding="utf-8")
    print(f"Wrote {len(df):,} rows, {out_path.stat().st_size / 1e6:,.0f} MB -> {out_path}")
