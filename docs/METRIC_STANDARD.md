# Metric standard and pipeline design

> **Status: proposed 2026-11-04. Phases 1 and 2 are built (§10, §11); the rest is not.** It describes the target. Sections 3 and 4 use normal insurance practice;
> the appointed actuary and finance manager should confirm them. Decisions still open are in §8.

## 1. The rule

1. **One name, one definition, one number.** A measure returns the same value for the same year and segment on every page.
2. **A page never changes a measure.** Pages choose which measures to show. They do not redefine them.
3. **A different view gets a different name.** "Reported claims to date" and "Ultimate claims" are two measures, shown side by side when both help.
4. **Premium and claims must be on the same basis** before they are divided.
5. **No measure depends on a slicer being set.** With no year selected, every year-based measure shows the current year.
6. **The pipeline stops if the numbers disagree** (§7).

## 2. How to read a number: three questions

Every figure answers three questions, and its name says the answers:

| Question | Options |
|---|---|
| **Which time basis?** | Underwriting year, accident year, or calendar year (§3) |
| **How complete?** | Paid → reported → ultimate (§4) |
| **Real or projected?** | Actual result, or a scenario for next year (§6) |

## 3. Time basis: the insurance default

| Basis | Groups by | Premium | Claims | Normally used for |
|---|---|---|---|---|
| **Calendar / financial year** | The accounting period | Earned in the period | Paid plus the change in reserves in the period | Financial statements and the P&L |
| **Accident year** | When the accident happened | Earned in the year | Ultimate cost of accidents in the year | Monitoring results and reserving |
| **Underwriting (policy) year** | When the policy started | Whole premium written | Ultimate cost of those policies, whenever the accident happens | Pricing, profitability of a year's business |

All three are correct, and for one year they give different numbers. For 2024: 505.0M earned in the calendar year, 358.9M written in the underwriting year.

**This report's basis: underwriting year**, by manager direction, pending the finance manager's approval (decision #8 in `docs/TASK_PLAN.md`). The report shows that one basis as its headline, and states on the page how it reconciles with finance's calendar-year figure.

## 4. Insurance concepts

| Concept | Default definition |
|---|---|
| **Written premium** | Premium of policies starting in the period |
| **Earned premium** | The written premium for the days of cover already passed, split by days |
| **Unearned premium** | Written − earned |
| **Exposure** | Vehicle-years of cover, earned the same way as premium |
| **Paid claims** | Cash paid so far |
| **Case reserve** | Claims handlers' estimate of what is still to pay on known claims |
| **Reported (incurred) claims** | Paid + case reserve |
| **IBNR** | Allowance for claims not yet reported and for growth on known claims |
| **Ultimate claims** | Reported + IBNR: the final expected cost |
| **Development** | How reported claims grow to ultimate with age. Mature periods: chain-ladder. Immature periods: usually Bornhuetter-Ferguson, which relies on an expected loss ratio until real claims build up |
| **Loss ratio** | Ultimate claims ÷ premium, same basis |
| **Expense ratio** | (Commission + admin) ÷ premium |
| **Combined ratio** | Loss ratio + expense ratio. Above 100% is an underwriting loss |
| **Frequency / severity / pure premium** | Claims ÷ exposure; cost ÷ claims; frequency × severity |
| **Credibility** | How much to trust a segment's own results vs its wider group (`docs/CREDIBILITY.md`) |
| **Actual vs expected (A/E)** | Claims so far ÷ what was expected by now. The usual early warning, because it compares like with like while a year is still developing |
| **Trend** | Expected yearly change in frequency and severity |
| **On-levelling** | Restating past premium at today's rates before comparing it with claims |
| **Technical price** | Pure premium ÷ (1 − expense rates − margin), on developed and trended claims and on-level premium |
| **Retention / elasticity** | Share of customers who renew, and how that responds to price. Estimated from renewal history |

## 5. The measure dictionary: the only base measures

Base measures read one table, `fact_segment_year`, one row per segment and underwriting year. Every other measure is built from these.

| Measure | Definition | 2024 today |
|---|---|---|
| `Written Premium` | Whole premium of policies written in the year | 358.9M |
| `Earned Premium To Date` | Part of it earned by the data cut-off | Not built |
| `Paid Claims` | Paid to date, those policies | 154.1M |
| `Reported Claims` | Paid + case reserve | 159.5M |
| `Claims Still To Come` | `Ultimate Claims` − `Reported Claims`: IBNR plus claims on cover not yet run. On this basis it is not pure IBNR, which needs Phase 3 | 157.7M (provisional) |
| `Ultimate Claims` | Final expected cost | 317.2M (provisional) |
| `Commission`, `Admin Expense`, `Expenses` | Whole-year expenses | 53.4M total |
| `UW Result` | Premium − ultimate claims − expenses | −11.6M (provisional) |
| `Loss Ratio`, `Expense Ratio`, `Combined Ratio`, `UW Margin` | Ratios of the above | 88.4%, 14.9%, 103.2% (provisional) |
| `Reported Claim Count`, `Ultimate Claim Count` | Counts | 29,109 and 55,552 (provisional) |
| `Policy Count`, `Earned Premium To Date` | Not built yet | |
| `Today Premium`, `Today Claims`, `Today Expenses`, `Today Result`, `Today Combined Ratio` | The same measures pinned to the current year, whatever year is selected. For alerts and pricing | 358.9M, 317.2M, 53.4M, −11.6M, 103.2% |
| `In-Year Premium`, `In-Year Claims`, `In-Year Expenses`, `In-Year Combined Ratio`, `Reliable Combined Ratio` | Results so far inside the year: the alerts' supporting line | 172.5M, 152.4M, 25.7M, 103.3%, 103.4% |
| `Exposure`, `Frequency`, `Severity`, `Pure Premium` | Ultimate basis | |
| `Expected Claims To Date`, `A/E` | Monitoring on claims so far | 122.4M, 1.25 (inside 2024, needs rebuild) |

**Year rule:** all base measures show the selected year, and the current year when none is selected.

**Allowed variants** have their own names and never reuse a base name:

| Prefix | Meaning | Example |
|---|---|---|
| `Calendar …` | Finance's view, for reconciliation only | `Calendar Earned Premium` = 505.0M |
| `Next Year No Change …` | Forecast, no action | `Next Year No Change Combined Ratio` |
| `Next Year Recommended …` | Forecast at the recommended price | `Next Year Recommended Premium` |
| `Scenario …` | A manager's What-If input | `Scenario Combined Ratio` |

## 6. The pipeline: one thing, done once

| Layer | Step | Produces | Rule |
|---|---|---|---|
| **1 Source** | Extract, prepare | Policies, claims, triangles | Raw facts, no business logic |
| **2 Calendar** | Report window | Current and forecast year, data cut-off | Decided first, read by everything |
| **3 Premium and exposure** | Earning | Written, earned, unearned | One earning calculation |
| **4 Claims to ultimate** | **Reserving** (new, single step) | Reported, IBNR, ultimate by underwriting year and segment; development pattern | **The only place ultimate claims are calculated** |
| **5 Segment results** | Results | `fact_segment_year` | Layers 3 + 4 + expenses; carries credibility |
| **6 Monitoring** | A/E and alerts | Flags: current status, future | Reads layer 5, adds nothing to ultimate |
| **7 Forecast** | Next-year premium, frequency, severity | Trended, on-level expected claims by segment | Starts from layer 4, one trend, tested on past years |
| **8 Pricing** | Technical price, then the optimizer | Next-year scenarios and recommended price | Uses layer 7's claims; never its own claim estimate |
| **9 Levers** | Path to Profitability | Lever sizes | Uses layer 5 |
| **10 Reconcile** | **Gate** (new) | Pass/fail report | Stops the run if any check fails (§7) |

**Flow for the manager:** results (layer 5) → flagged by monitoring (layer 6) → drill through → card: Today (layer 5), No change (layer 7), Recommended (layer 8).

## 7. Reconciliation gate

The run fails when any of these is off by more than rounding:

- Segments add up to the portfolio, and zones add up to the portfolio, for premium, claims and result.
- Written premium for the year equals the sum of monthly earned premium for those policies.
- Reported + IBNR = ultimate. Ultimate ≥ reported.
- Levers' baseline = segment results for the current year.
- Optimizer's today book = segment results, for premium and policies.
- The Forecast page's last 12 months = segment results for the current year.
- Every step uses the same current year and data cut-off.
- Bridge to finance: `Written Premium` − not yet earned + earned from earlier policies = `Calendar Earned Premium`.

## 8. Where the report is today

| # | Breaks the rule | Evidence | Fix |
|---|---|---|---|
| 1 | One number under many names | 358.9M is `Total Earned Premium`, `Today Premium`, `Cur Premium`, `PTP Baseline Premium`, `CY Earned Premium`, `Premium Last Year` | One `Written Premium` |
| 2 | Wrong name | `Total Earned Premium` includes premium not yet earned; `Alert Earned Premium` = 172.5M | Rename to the dictionary |
| 3 | "Ultimate" means two things | `Total Ultimate Claims` 317.2M; `CM Total Ultimate` 159.5M with `CM Total IBNR` = 0; `fact_claim[ULTIMATE_CLAIM]` is really reported claims | Reported vs ultimate |
| 4 | Depends on the slicer | No year selected: `Total Earned Premium` 2,251M, `Combined Ratio` 99.5% | Year rule |
| 5 | Years not comparable | 2020–23 are reported with no IBNR, though labelled "Actual developed"; 2024 is an estimate | Reserving step for all years |
| 6 | Ultimate calculated in several places | Step 01 (none), step 05, step 07 (chain-ladder), step 09, step 11 | One reserving step |
| 7 | Claim counts differ | 55,552, 29,109, 27,733 | Named variants |
| 8 | Small gaps from copies | `PTP Current CR` 103.242% vs 103.241%: a rounded column in the lever file | No rounding in stored facts |
| 9 | Typed values | `TARGET_CR` 0.85 in step 06, `LATEST_MATURE_YEAR`, `UW Target Severity` 0.85, action thresholds in measures | Company settings |
| 10 | Too many measures | 355 measures: 24 named for premium, 51 for claims, 29 for combined ratio | Base + derived, retire the rest |

**Compared with the insurance default**

| Item | Today | Default | Gap |
|---|---|---|---|
| IBNR | None in claims; only the Forecast develops | Part of every ultimate | **Largest gap.** Corrected development would move the recommended result from +9.0M to −30.5M (`docs/PAGE_RECONCILIATION.md` §10) |
| Immature year | In-year loss ratio × whole premium, a demo method | Bornhuetter-Ferguson | Replace |
| Triangles | By accident quarter | Underwriting-year triangles too, for this basis | **New extract needed** |
| Monitoring | Actual combined ratio inside the year | A/E | Replace, simpler to explain |
| Trend | One portfolio factor from the forecast | Frequency and severity trends | Build and test |
| On-levelling | None | Standard for pricing | Needs rate-change history |
| Retention | Hand-set | Estimated from renewals | Blocked on a stable renewal link |
| Gross vs net | Premium and claims look gross of reinsurance; recoveries are not used | Report both | **Confirm with finance** |

**Decisions needed**

| Decision | Who |
|---|---|
| Underwriting year as the report's headline basis | Finance manager (decision #8) |
| Development method, and the IBNR for each year | Actuary (decision #6) |
| Which premium column is the standard (`VUW_TOTAL_PREM`, gross, net), and gross or net of reinsurance | Finance manager |
| One target combined ratio | Manager (decision #1) |
| Alert rules and price rule now in the demo | Manager (decision #9) |

## 9. Build order

| Phase | Work | Result | Depends on |
|---|---|---|---|
| **1 Measures** | Dictionary in Power BI; pages repointed; old names retired; year rule | Same number on every page, no method change | Nothing |
| **2 Gate** | Reconcile step in `run_all.py`; no rounding in facts; typed values to settings | Bad runs stop | Phase 1 |
| **3 Reserving** ✅ *built 2026-11-10, provisional* | One step for ultimate claims, using accident-quarter triangles first; all years comparable | Honest claims; steps 05, 07, 09 read it (`14_reserving.py`, `fact_claims_ultimate`); step 11's alerts stay on in-year results | Actuary's method (decision #6) |
| **4 Underwriting-year triangles** | New extract and Bornhuetter-Ferguson | Default method on the report's basis | DBA, actuary |
| **5 Forecast and pricing** | Segment trend; on-level; technical price; A/E alerts | Pricing on the default method | Phases 3–4, rate history |
| **6 Retention** | Estimate from renewals | Reliable customer reaction | Renewal link |

## 10. Phase 1: built in the report (2026-11-04)

The dictionary is in the folder **01 Base Measures**. Old names still work: each is now a hidden pointer to the new measure, so every visual already shows the one number. The claims method and prices did not change.

**What changed**
- With no year selected, measures show the current year: `Written Premium` 358.9M (was 2,251M), `Combined Ratio` 103.2% (was 99.5%). A selected year still works (2023: 607.1M, 80.8%).
- A matrix total across several years, with no year selected outside it, now shows the current year. Add a year filter to the visual if a multi-year total is needed.
- `Cur …` and `PTP Current CR` now read the same table as Profitability. This removed gaps of about 25 SAR and 0.001 points. The What-If bridge's steps now differ from the scenario profit by 25 SAR, the optimizer file's rounding.
- `UW Cost Component Value`: the build-up now adds up (paid 154.1M + case reserve 5.4M + still to come 157.7M = ultimate 317.2M). Before, IBNR was 0 and "Ultimate" showed 159.5M. The "IBNR" row is now "Still to come".

**To repoint in Desktop.** Optional for the numbers; needed for the right names and before old names can be deleted.

| Page | Visual | Title | Now uses | Change to |
|---|---|---|---|---|
| Profitability | Column chart | Where the book earns and loses. | `Total UW Profit` | `UW Result` |
| Profitability | Card | (no title) | `Total UW Profit` | `UW Result` |
| Profitability | Card | (no title) | `Total Expenses` | `Expenses` |
| Profitability | Deneb | Underwriting Performance by Segment | `Total Earned Premium` | `Written Premium` |
| Profitability | Deneb | Underwriting Performance by Segment | `Total UW Profit` | `UW Result` |
| Profitability | Deneb | Underwriting Performance by Segment | `Profit Margin` | `UW Margin` |
| Profitability | Card | (no title) | `Total Earned Premium` | `Written Premium` |
| Profitability | Card | (no title) | `Total Ultimate Claims` | `Ultimate Claims` |
| Path to Profitability | Card | Combined ratio today | `PTP Current CR` | `Today Combined Ratio` |

**Deneb visuals:** the spec reads fields by name, so after swapping a field use **Rename for this visual** to give it the old name, or update the spec. Don't swap them without that, or the visual loses its data.

**Also needs attention**
- `CM IBNR %` and `CM Segment IBNR %` on Claims Mgmt read 0% because no IBNR exists per claim. The "IBNR uncertainty" card should be hidden or relabelled until Phase 3.
- `Premium Last Year` and the other `… Last Year` measures compare the same months as the forecast window. They equal the base measures only at 12M: a different measure, so they keep their own name.
- `FC Total Premium` (382.9M, all policies expected in 2025) and `Card NoAction Premium` (323.0M, 2024 policies renewing) are both next-year premium on different scopes. Name them `Forecast Written Premium` and `Next Year No Change Premium` when repointing.

**Deliberately not changed**
- Measures on the lever tables (`What We Charge`, `CM Segment Ultimate`, `BM Claims This Segment`, `UW Claims In Segment` and others). Those tables group by the lever table's own columns, so base measures would not filter them. The values match: Phase 2's gate checks this.
- 104 measures no visual uses (old `Sim …`, `BE …` and others): retire them after the repointing.

## 11. Phase 2: the gate, unrounded facts and settings (2026-11-04)

**The gate: `pipeline/13_reconcile.py`.** `run_all.py` always closes a run with it, even with `--only`. Any failure makes the run fail: **do not refresh the report from a failed run.** Results go to `02_facts/fact_reconciliation.csv`. Two amounts agree within the larger of `[reconcile] tolerance_sar` (1 SAR) and `tolerance_share` (0.001%).

| Group | Checks |
|---|---|
| Calendar | Report window, forecast year, optimizer year and target all match |
| Premium | Segment results = earned table, every policy year |
| Claims | Reported = claim table; ultimate not below reported; result = premium − claims − expenses |
| Zones | Every segment's zone exists; every office has a location |
| Levers | Premium and claims = segment results, per segment and in total |
| Optimizer | Today premium, claims and expenses = segment results; each recommended price is a calculated scenario |
| Forecast | Each full year's months = segment results; next-year loss ratio = current × trend |
| Alerts | In-year premium = earned table; segment claims = whole premium × reliable loss ratio |
| Report copies | Claim copy = reported claims of the current year |
| Bridge | Written − earned outside the year + other policies' earned in it = calendar earned |

Real data: 58 of 58 pass. Tested by breaking the data four ways (levers, segment claims, forecast history, optimizer book): each one failed the run, and restoring the file passed it.

**The demo data fails 10 checks.** They look like real gaps in that data, not in the gate, but the causes are not fully traced: its optimizer has premium and expenses for Group Medical segments that its segment results show as zero, and its today claims also differ on motor segments; its forecast premium is 7-8% above segment results in 2020-2022; and its in-year alert premium is 4% above the earned table. Not yet fixed, so `run_all.py --source synthetic` ends as failed.

**Rounding removed** from stored facts in steps 06, 07 and 11. Largest change in any stored amount: 0.005 SAR. Kept: claim counts as whole numbers in step 05; GLM coefficients in its report; the join key in step 10.

**Moved to `config.toml`:** `[business]` target and rates; `[credibility]`; `[safe_zone]`; `[levers]`; `[optimizer]`; `[forecast]`; `[report_tiers]`; `[variance]`; `[reconcile]`. Step 12 passes the ones the report needs to `dim_report_window`, and measures read them there. `LATEST_MATURE_YEAR` is now calculated from the current year. Same values as before, with one exception:

- **Lever target 85% → 95%**: one target in `[business]`, the value the optimizer and report already used (decision #1 still open). `gap_sar` total 65.5M → 29.6M, distribution lever 1.11M → 0.74M. Other levers: rounding only.

**Still in code, as named method constants:** backtest and validation settings in steps 04 and 07, the price grid in step 09, and the alert windows and thresholds in step 11. The demo builder (step 01) keeps its own assumptions.
