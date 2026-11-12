# Other lines: data checks before pricing (plan F2)

Run 2026-11-12 with `tools/check_other_lines.py`: read-only, totals only, policy years 2019 onward,
output in `data/raw/checks/other_lines_*.csv`. FGA = Fire, Liabilities, Engineering, General Accident.

## Findings

### 1. Fire's 1,149M premium is a data error, not a big book

| | Policies | Total premium | Gross premium | Sum insured |
|---|---:|---:|---:|---:|
| Fire, policy years 2019–2024 | 103–461 a year | 7.5M–15.4M a year | same | 3.5bn–15.3bn |
| Fire, policy year 2026 | 964 | **1,079M** | **4,013,353M** | **20,009,397M** |

Real Fire is about 10–15M a year. 94% of the 1,149M sits in policy year 2026: after the data's cut-off
(January 2025), with gross premium and sum insured that are impossible (trillions). The reinsurance
bordereau has the same 2026 rows (premium 1,005,890M). General Accident and Liabilities also have odd 2026 rows
(gross 34.8M and 22.6M vs total premium 1.7M and none).
**Action:** any pricing excludes policy years after the cut-off (the pipeline already does: step 19 flags
them, the current year comes from the claims). **For the DBA:** what are the policy year 2026 rows in `V_UW_DATA`
and `VW_REI_PRODUCTION_BORDEREAUX_FGA`? (Test data, placeholders, or a load error?)

### 2. Premium and claims are both gross of reinsurance

- `V_UW_DATA` total = gross = net premium for FGA (its "net" is net of discount, not of reinsurance).
- `V_CLM_DATA` claims carry almost no recoveries (`RECOVERY_AMT` 0.1M in total); the reinsurers' share sits
  in `VW_REI_CLAIMS_RECOVERY` (Fire treaty estimates 115M ≈ Fire's gross claims).
- So loss ratios from these two views are gross / gross: consistent. ✓
- The company keeps little of the big classes (retained premium ÷ policy premium in the bordereau):

| Class | Kept | Ceded mostly to |
|---|---:|---|
| Fire | about 14% | quota share, surplus, facultative |
| Engineering | about 20–25% | quota share, surplus |
| General Accident | about 45–95% | facultative |
| Liabilities | about 50–90% (lower since 2023) | facultative, quota share |

**For the manager:** price on gross (the technical price the customer pays) and add the reinsurance cost as a
load, or price the net retained book? Gross is the usual basis for a technical price.

### 3. Policy year 2024 is incomplete in `V_UW_DATA` for FGA

| Class, 2024 | `V_UW_DATA` | Reinsurance bordereau |
|---|---:|---:|
| Fire | 7.5M (103 policies) | 29.0M |
| Engineering | 2.8M | 13.3M |
| Liabilities | 8.4M | 27.3M |
| General Accident | 0.6M | 2.3M |

For 2019–2023 the two sources agree within a few percent (Fire 2019: 9.56M vs 9.65M). In 2024, about
three quarters of FGA premium is missing from the underwriting view.

### 4. Why 15% of FGA claims miss their policy: all of them are policy year 2024

| Reason | FGA claims | Estimate | Marine claims |
|---|---:|---:|---:|
| Matched | 1,089 | 30.1M | 232 |
| Same 6-part key, another policy year | 30 | **96.0M** | – |
| Same number, year and class; other office / type / source | 155 | 1.3M | 9 (2.9M) |
| Same number and year; other class | 11 | 0.2M | – |
| Only in the loss-ratio view | 4 | 0.0M | – |
| No policy anywhere | 0 | – | – |

Every unmatched claim is policy year 2024: the same year the underwriting view is incomplete (finding 3).
The biggest group is 30 claims whose policy exists under another policy year. They include Fire's 92.6M
claim, which is three times Fire's whole 2024 premium in the bordereau. The 155 + 9 "other office / type / source"
claims are mostly Liabilities, and they match on policy number, year and class.
**Fix we can make:** a second match on policy number + year + class when that is unique (recovers 164
claims). **For the DBA:** where are FGA policy year 2024 policies in `V_UW_DATA`? Are they stored under 2026
(finding 1)?

### 5. A handful of policies and claims decide each year

- Fire: the 10 largest policies are 30–40% of premium (83% in 2024); single claims dominate (2021: one claim of
  14.6M out of 15.0M; 2024: one of 92.6M out of 93.6M).
- Engineering 2024: one claim of 2.5M out of 4.7M. General Accident: one policy is up to 64% of a year.
- Marine Hull: one or two policies a year, so it **can't be priced from data**.

## What this means for F1 (pricing FGA and Marine)

| Class | Claims a year | Can F1 price it? |
|---|---:|---|
| Liabilities | 33–359 | Yes: credibility loss ratio, after the 2024 fixes |
| Marine Cargo | 12–109 | Yes, with low credibility |
| Fire, Engineering, General Accident | 1–17 | Only with a large-loss cap, and the excess spread over years / a benchmark. Credibility stays low, so the complement (benchmark loss ratio) is a manager setting |
| Marine Hull | about 1 policy | No: manual / reinsurer rate |

Before F1:
1. Exclude policy years after the cut-off (already the pipeline rule).
2. Add the second claim match (number + year + class).
3. The DBA answers the two questions above (2026 rows; missing 2024 FGA policies). Until then, use FGA
   2019–2023 only, or take 2024 FGA premium from the reinsurance bordereau.
4. The manager decides gross vs net pricing and the benchmark loss ratios (parked for the end-of-prototype review).
