# Data cut-off, maturity and freshness: design

> **Status: ⚠ NEEDS MANAGER DECISION.** Designed 2026-10-27, not built. To be reviewed with the manager
> when the prototype is finished (`docs/TASK_PLAN.md` Task 1, decision #5). The proposed defaults (§3–§6) are
> the starting point for that review; item 3 (claims maturity) also needs the actuary, and two data questions
> are open with the DBA (§7). Ready-to-send messages for both are in §9. The defaults are written so they can
> be changed in `config.toml` without touching code.

## 1. The problem

The product is sold to many insurers. Each one loads its own data, so **no step may assume which year or
month is "current"**. Today several steps do:

| Step | How it decides "now" today |
|---|---|
| `11_build_alerts.py` | ✅ From the data: latest accident date, partial or under-reported month dropped |
| `12_report_window.py` | ✅ From the data: current year = last calendar year the claims reach to its end (added 2026-11-01) |
| `07_forecast_seasonal.py` | `MATURE_DATA_END = "2023-09-30"`, typed in code |
| `05_segment_performance_by_year.py` | `LATEST_MATURE_YEAR = 2023`, typed in code |
| `04_backtest.py` | backtest points 2019-12-31 to 2024-06-30, typed in code |
| `settings.valuation_date()` | the **date of the extract file** (2026-10-25), although the data ends 2025-01-18 |

## 2. What the company extract contains (checked 2026-10-27)

| | Available | Not available |
|---|---|---|
| Claims | Accident date (100% filled), claim year, total paid, total estimate | Report date, payment/transaction dates; `CLOSE_DATE` is empty in all 326,787 rows |
| Policies | Effective date, expiry date | Issue/booking date |
| History | One snapshot per extract | Payment and reserve history over time |
| Runs | `data/run_log.csv` records every run | — |

**Finding:** the development factors the pipeline writes (`fact_dev_factors_paid`) are **not valid**:
the 12→24-month factor is **434×**. A single snapshot with no payment or closing dates cannot produce a
development triangle. Until there is transaction history (§7), these factors must not be used; the
settlement-speed check that reads them is switched off.

## 3. Cut-off date (decision 1)

Each analysis uses the date that fits its purpose:

| Purpose | Date | Why |
|---|---|---|
| Claims experience (alerts, loss ratios, forecast) | **Accident date** | Standard basis for experience; the only claim date we have |
| Exposure and premium | **Policy effective date** | When cover starts |
| Data freshness | Report or transaction date when available; **accident date until then** | Shows when data last *arrived* |

**Data as-of date** = the earlier of the claims cut-off and the policies cut-off. Today: 18 Jan 2025.
`settings.valuation_date()` returns this instead of the file date. `[prepare] as_of_date` in config still
overrides it when set.

## 4. Complete month (decision 2)

A month is used only when all three tests pass:

1. **Calendar:** the data reaches the month's last day.
2. **Coverage:** its claim frequency (claims per SAR of earned premium) is at least 70% of the average of the
   12 months before (method constant `LATE_REPORTING_TOLERANCE = 0.30`, as in the alerts step). Trailing
   months that fail are dropped.
3. **Ingestion (across runs):** the cut-off has moved forward since the previous run of the same source in
   `run_log.csv`. If not, the run is flagged (§6).

Today: Jan 2025 fails test 1 (data ends on the 18th) and test 2 (1,409 claims against about 6,000 in a normal
month). **Last complete month: Dec 2024.**

## 5. Claims maturity (decision 3, interim, for the actuary to confirm)

A policy year is **mature** when both hold:

| Test | Setting (`config.toml`, `[maturity]`) | Default | Today |
|---|---|---|---|
| Fully earned by the last complete month: share of its earned premium on or before that month | `min_earned_share` | **0.99** | 2023: 100%, 2024: 48% |
| Claims mostly settled: paid ÷ incurred | `min_paid_ratio` | **0.98** | 2023: 98.3%, 2024: 96.6% |

Result today: **mature through 2023**, the same as the hard-coded rule it replaces, now worked out from the
data, so it moves forward on its own. The two thresholds are the actuary's to set. The proper method
(chain-ladder development) needs claim transaction history (§7).

## 6. Freshness and missing periods (decisions 4 and 5)

**Freshness.** Company setting `[data] refresh_months` (default **1**: monthly). Each run writes
`fact_data_status.csv` and `run_all.py` reports:

| Status | When | Today |
|---|---|---|
| OK | as-of date within `refresh_months + 1` months of the run date | |
| **Stale** (warning) | older than that | ✔ 21 months old |
| **Not updated** (error) | as-of date unchanged since the previous run of the same source | ✔ |

**Missing periods.** Never filled with made-up actuals:

| Analysis | Treatment |
|---|---|
| Alerts, loss ratios, credibility | Excluded: windows end at the last complete month |
| Forecast | Starts from the last complete month; months up to today are shown as **estimated**, dashed |
| History charts | Shown as a gap, with a "data ends here" marker |
| Every page | Banner: *"Data as of 18 Jan 2025 · last complete month Dec 2024"*, plus a warning when stale |

## 7. Open items

**For the actuary**
- Confirm or set the two maturity thresholds (§5).
- Agree the development method once transaction history is available.

**For the DBA**
- Why do `V_UW_DATA` and `V_CLM_DATA` stop in early 2025 (policies 6 Mar 2025, claims 18 Jan 2025)?
  Are they still being loaded, or are current data in other tables?
- Add the claim report date and payment/reserve transactions (amount and date) to the claims extract,
  or confirm they are not stored.

## 8. Build plan

| File | Change |
|---|---|
| `pipeline/data_status.py` (new) | Cut-offs, last complete month, maturity, freshness; writes `fact_data_status.csv` |
| `pipeline/settings.py` | `valuation_date()` from the data |
| `run_all.py` | Runs the data-status check after step 1; prints the status; flags Stale / Not updated |
| `05_segment_performance_by_year.py` | Maturity from §5 instead of `LATEST_MATURE_YEAR` |
| `07_forecast_seasonal.py` | Mature window and forecast origin from §4–§5 instead of `MATURE_DATA_END` |
| `04_backtest.py` | Backtest points derived from the data span |
| `11_build_alerts.py` | Uses the shared last-complete-month function; settlement-speed check off |
| Power BI | Table `fact_data_status`; banner measure `Data Status Text` |

## 9. Messages for the actuary and the DBA

Ready to send (drafted 2026-10-27).

**To the actuary — "Actuarial input needed: when is a year's claims experience final enough to use?"**

> The dashboard judges each segment's profitability from its claims experience. For older years the claims are
> essentially final; for recent years claims are still being reported and settled, so the figures look better
> than they will end up. Treating a recent year as final would overstate profitability and could lead to price
> cuts in segments that are actually losing money.
>
> We propose that a year counts as final when at least 99% of its premium has been earned and at least 98% of its
> claims cost has been paid. On current data this marks 2023 and earlier as final, and 2024 as not yet final.
>
> We need you to: (1) confirm these thresholds or set different ones; (2) agree the method to use once claims
> payment history is available, a standard development method (chain ladder) that estimates the final cost of
> recent years instead of excluding them. The current extract cannot support that method: the development
> factors it produces are unusable (one shows claims multiplying 434 times in a year).

**To the DBA — "Data request: motor policy and claims views appear to stop in early 2025"**

> The dashboard reads motor business from `DTWCDE.V_UW_DATA` (policies) and `DTWCDE.V_CLM_DATA` (claims). The
> extract taken on 25 Oct 2026 shows the latest policy start date is 6 March 2025 and the latest accident date is
> 18 January 2025. Our extract applies no date filter, so the cut-off is in the views themselves, and every page
> currently describes the business as it stood in January 2025.
>
> 1. Are these two views still being loaded? If not, where are motor policies and claims written since early
>    2025 stored?
> 2. Do the claims tables hold the claim report date and the payment and reserve transactions (amount and date of
>    each movement)? We need these to measure how quickly claims are reported and settled, and to estimate the
>    final cost of recent claims properly.
