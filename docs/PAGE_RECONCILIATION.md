# Why the pages showed different numbers, and what was fixed

> **Checked 2026-11-03** against `clean model.pbix` (Desktop) on the Oracle data (claims to 18 Jan 2025,
> current policy year 2024, report window 2020–2024).

## 1. The one rule every page now follows

**"Current" means the current policy year** (`dim_report_window[CURRENT_YEAR]`, worked out by
`settings.current_year()`: the last calendar year the claims reach to its end). The Profitability page
defaults to that year, the optimizer prices that year's book, and the lever table is built from it.

## 2. What was wrong

| # | Where | Problem | Effect | Status |
|---|---|---|---|---|
| 1 | `06_lever_allocation.py` | Read the all-years `fact_segment_performance.csv` (policy years **2019–2026**, incl. a year outside the report window, the part-year 2025 and a future 2026) | Path to Profitability / Repricing / Bonus-Malus / UW / Claims pages sized the levers on **2,587.5M** premium; the portfolio pages show **2,251.0M** (2020–24) or **358.9M** (2024) | **Fixed**: reads `fact_segment_performance_by_year.csv`, current year only |
| 2 | `PTP Current CR` | `= [Combined Ratio]` with no year filter on the page → all 5 years | Path to Profitability started at **96.8%** while Profitability (2024) shows **86.5%** | **Fixed**: computed from the lever table (current year) |
| 3 | `PTP * CR Pts`, `PTP * Raw` (9 measures) | Lever SAR (8 years) ÷ `[Total Earned Premium]` (5 years) | Lever CR points overstated by ~15% and on a different book from the start CR | **Fixed**: divide by new `[PTP Baseline Premium]` |
| 4 | Relationships | `fact_claim` was not linked to `dim_zone_summary` or `dim_business_class` | Claims Mgmt / UW / Bonus-Malus cards built on claims (e.g. *Open claims*) ignored the Zone and Class slicers | **Fixed**: `dim_zone[ZONE] → dim_zone_summary[ZONE]`, `fact_claim[COB_DESC] → dim_business_class[CLASS_DESC]` |
| 5 | Report: page filters | **Corrected 2026-11-04: not a cause.** The Zone / Class values saved on Optimal Price and Safe Zone are marked `howCreated 5` (read as drill-through filters), change between saves, and the pages showed all segments in the 2026-11-03 screenshots. No evidence they changed a number | None | Withdrawn |
| 6 | Report: slicers | Zone / Class / Product slicers are not synced between pages; Target CR is **82%** on Path to Profitability and **95%** (default) on Forecast and Optimal Price | Same filter, different numbers per page | **To do in Desktop** (§4) |

## 3. After the fix (no slicers, current year 2024)

**Today's book (policy year 2024) is one set of numbers on every page that shows "today":**

| | Profitability | Path to Profitability | Optimal Price / What-If / Safe Zone "Today" |
|---|---|---|---|
| Premium | 358,936,339 | 358,936,339 | 358,936,339 |
| Expected claims | 254,845,383 | 254,845,383 | 254,845,383 |
| Expenses | 53,394,827 | — | 53,394,802 (rounding in the optimizer file) |
| UW profit | 50.70M | — | 50,696,154 |
| Combined ratio | 85.88% | 85.88% | 85.88% |

How it was made one number (2026-11-03, second pass):

- **Pricing pages.** `Cur Premium / Claims / Expenses / Retained Exposure / Retention` and `WI Cur
  Retained Policies` now show today's book: the optimizer's 0% row divided by `[Baseline Renewal]`
  (`fact_optimizer_run[baseline_renewal]`, 90%). Exact, because at 0% price change every policy renews
  with that probability. The **Scenario** column is unchanged, so at 0% it shows the 10% expected lapse
  (premium 323.0M, customers kept 90%). `WI Bridge Value` starts from today's book; its *Volume
  (retention)* step now includes the lapse, and the steps still add up to the scenario profit.
- The What-If and Safe Zone cards' first column is titled **Today** (was *No change*); the profit rule
  reads *"Profit at least the no-price-change level"*, which is what the optimizer tests. Done by
  wrapper measures `WI Impact HTML` / `Safe Zone Card HTML` over the hidden `… Body` originals.
- **Claims.** 49 policy-year-2024 records with **zero premium** (fleet headers, flat cancellations)
  had model frequencies of ~12.7 claims a year each: 2.09M of claims with no premium. The optimizer
  already dropped them; `05_segment_performance_by_year.py` now does the same (model claims only on
  policies with premium > 0 and exposure > 0). Profitability 2024 claims 256.94M → 254.85M.

**Calendar-year figures are a different measure, not a different value of the same one:**

- **Forecast** "Last Year" = premium **earned** in Jan–Dec 2024 from all policies in force (505.0M); a
  monthly forecast can only be compared on calendar months. Its expected 2025 premium (374.6M) follows
  the 2024 run-rate (51.7M in Jan → 30.5M in Dec). Label these cards *"Earned Jan–Dec 2024"*.
- **Management Alerts** use the same calendar window by accident date (premium 505.0M,
  `docs/ALERTS_METHOD.md`).

## 3a. Zone "Other" was hidden on the Profitability page

The Profitability zone chart, both zone/class tables and the segment grid each have a **visual-level
filter excluding `Other` and `Unspecified`**; the KPI cards do not. "Other" is not a region: it is
every office whose name has no city (`zone_mapping.py`): Tameeni Web aggregator, E-Commerce, Gonsure,
telesales, outlets. In 2024 it holds **199.5M premium (56%) and 32.6M of the 50.7M UW profit**, so the
zone columns summed to 18.1M against the 50.7M card. Remove those visual filters (§4) and the columns
add up to the card.

## 4. To do in Power BI Desktop (report layout, not reachable from the model)

> **Correction 2026-11-04:** the page-filter items in this section (Optimal Price, Safe Zone) are withdrawn; see row 5 of §2.
> Before saving, a page reached by drill-through can still keep that segment, so reset it if the page shows one segment.

> **Recheck of the 2026-11-03 09:55 save:** model changes all saved; Optimal Price page filter cleared;
> Profitability zone column chart includes Other. Still open: Safe Zone has a new page filter
> (*Western · Comprehensive · Motor Rent a Car*, premium 14.1M instead of 358.9M); on Profitability
> the two zone/class tables and the segment grid still exclude Other, as do the segment tables on
> Bonus-Malus, UW and Claims Mgmt and the zone chart on Bonus-Malus. (Slicers that hide Other from their
> list are harmless: an empty slicer filters nothing.)

1. **Optimal Price** and **Safe Zone** → Filters pane → *Filters on this page* → clear the Zone and
   Class values (or delete those filters; the slicers on the page already do the job).
1. **Profitability** → select the zone column chart, each zone/class table and the segment grid →
   Filters pane → *Filters on this visual* → `ZONE` → remove the *is not Other / Unspecified* filter.
   Give the segment grid's blank zone column a header (it shows *null*), e.g. *Online & other channels*.
1. **Forecast** → the six KPI cards → rename the *… Last Year* reference labels to *… Jan–Dec 2024*
   (Format → Reference labels; measure names are left as they are so no visual breaks).
2. View → **Sync slicers**: sync `dim_zone_summary[ZONE]`, `dim_business_class[CLASS_DESC]`,
   `dim_product[POLICY_TYPE_DESC]` across every page that has them. Safe Zone has two sets of these
   slicers; keep one.
3. Sync the **Target Combined Ratio** slicer between Forecast and Path to Profitability and set one value.
4. Save the file (the model changes in §2 are in the open session until it is saved).

## 5. Parked for the manager (not changed)

- **Claims basis.** `ULTIMATE_CLAIM` = paid + case reserve, **no IBNR** (`[prepare] ultimate_method =
  "incurred"`), yet `fact_segment_performance` labels 2020–23 *"Actual developed"*. The Forecast page
  develops claims by chain-ladder, so the same accident year 2024 is **423.6M** on Forecast and
  **371.0M** on Alerts (+14%). One method should feed every page; which one is the actuary's call.
- **Target CR.** Still four values (pipeline 0.85 hard-coded in `06_lever_allocation.py`, optimizer
  0.95, slider default 0.95, PTP slicer 0.82). Design is in `docs/TARGET_CR.md`, awaiting decision #1.
- **Closed claims.** Only 17 of 292,471 claims have a `CLOSE_DATE`, so `IS_CLOSED` is false for
  99.99% and *Open claims pipeline* / *IBNR uncertainty* on the Claims Mgmt page are not meaningful
  until the extract carries the close date (or a status column).
- `LATEST_MATURE_YEAR = 2023` is hard-coded in `05_segment_performance_by_year.py`; it should follow
  `current_year()` (or a development rule) so it moves forward with the data.

## 6. Every office has a zone (2026-11-03, third pass)

`pipeline/zone_mapping.py` now follows the team's office-location specification (city keywords with
latitude / longitude), plus three offices looked up by name: وزيرية → Jeddah, قيا → Taif (Qiya centre,
Taif governorate), رفيدة → Ahad Rafidah (Asir). Offices whose name has no city (Tameeni Web, Gonsure,
منافذ, E-Commerce, telesales, leasing, brokers) take `[zones] default_office_city` in `config.toml`
(Riyadh → Central). `dim_zone` gained `LATITUDE`, `LONGITUDE` (map data categories) and
`LOCATION_BASIS` (*City in office name* / *Default office city*): 62 and 14 of the 76 offices.

Zone is a GLM rating factor, so the whole pipeline was rerun (steps 1–12). Results for 2024:

| Zone | Premium | UW profit | CR |
|---|---|---|---|
| Central | 311.9M | 48.8M | 84.3% |
| Western | 34.7M | 4.5M | 87.1% |
| Eastern | 11.0M | −3.2M | 128.8% |
| Southern | 1.3M | 0.4M | 65.9% |
| Northern | 0.04M | 0.002M | 96.5% |
| **Total** | **358.9M** | **50.57M** | **85.91%** |

The zones add up to the cards, and today's book is the same on every page (premium 358,936,339; claims
254,970,805; CR 85.91%). Claims moved from 254.85M because the GLM was refitted with the new zones.
**Central is now 87% of premium**, mostly online channels: zone comparisons mean "online + Riyadh"
vs the branches. Whether online channels should be a zone of their own is a manager decision.

The real data's `dim_zone.csv` takes its columns from the synthetic one (`conform()` in
`01_prepare_real.py`), so the synthetic data was rebuilt too.

## 7. One report calendar for every page (2026-11-03)

**Rule.** *Current year* = the latest year whose 12 months the data covers in full. *Forecast year* =
current year + 1: the months of it the data already covers are actuals, the rest is forecast. When
the claims reach 31 Dec of the forecast year, both roll forward on the next pipeline run.

| Data to | Current year | Forecast year |
|---|---|---|
| 18 Jan 2025 (today) | 2024 | 2025 (all forecast) |
| 30 Jun 2025 | 2024 | 2025 (Jan–May actual, Jun–Dec forecast) |
| 31 Dec 2025 | 2025 | 2026 (all forecast) |

**Where it lives.** `settings.current_year()` decides; step 12 writes it to `dim_report_window`
(`CURRENT_YEAR`, `FORECAST_YEAR`, `FORECAST_ACTUAL_MONTHS`, `DATA_CUTOFF`). Power BI measures in the
**00 Report Calendar** folder read only that table:

| Measure | Today |
|---|---|
| `Current Year` / `Forecast Year` | 2024 / 2025 |
| `Current Year Label` | Current year 2024 · data to 18 Jan 2025 |
| `Forecast Year Label` | Forecast 2025 (adds "Jan–May actual, rest forecast" mid-year) |
| `Policy Year Label` | Policies written in 2024 (or the years selected) |
| `Earned Current Year Label` | Earned Jan–Dec 2024 |
| `CY Earned Premium` / `CY Claims` / `CY Loss Ratio` | 505.0M / 423.6M / 83.9% (calendar 2024) |
| `FY Premium` / `FY Claims` / `FY Loss Ratio` | 374.6M / 377.1M / 100.7% (actual + forecast 2025) |
| `Calendar Check` | ✓ All pages on current year 2024 and forecast year 2025 |

- `dim_policy_year[YEAR_ROLE]` (*Current year* / *Earlier years*): a slicer saved on *Current year*
  moves forward by itself; one saved on *2024* does not.
- `Expense Ratio` now uses the current year when no year is selected (was all 5 years: 13.8% → 14.9%).
  It feeds the Forecast page's underwriting result and target line.
- `Calendar Check` lists any page that has left the calendar: optimizer policy year (Optimal Price,
  What-If, Safe Zone), lever book (Path to Profitability and lever pages), forecast reaching Dec of the
  forecast year, and the alerts window. Alerts use the latest 12 complete months by design
  (`docs/ALERTS_METHOD.md`); mid-year the check will say so.
- `09_profit_optimizer.py` picks its year with its own `complete_years()` rule; the check catches it if
  that ever differs from `current_year()`.

**Desktop steps to finish the pages**
1. Profitability: set the year slicer to `dim_policy_year[YEAR_ROLE]` = *Current year* (keep the
   `POLICY_YEAR` slicer, empty, for drilling into one year).
2. Forecast cards: Format → Reference labels → title **fx** → `Earned Current Year Label`; card titles
   **fx** → `Forecast Year Label`. Save the Forecast window slicer on **12M** (or use the `FY …` measures).
3. Page subtitles: a card with `Current Year Label` (Forecast, Alerts), `Policy Year Label`
   (Profitability, Path, pricing pages).
4. Put `Calendar Check` on a card (e.g. the Forecast page header or an admin page).

## 8. Manager direction on the reporting basis (2026-11-04) — in the demo, needs finance approval

**The rule.** A year's figures count only policies written in that year, with their whole premium including what
continues after the year. For 2024: 358.9M on every page. Excluded: 332.5M earned in 2024 by earlier policies still
running. It replaces the two-basis proposal in §5, and an in-year version (172.5M) tried and dropped the same day
because a monthly view of it climbs through each year and resets every January.

**Status: shown in the demo, flagged as needing the finance manager's approval.** Finance reports 505.0M (earned in
calendar 2024), so the dashboard differs on purpose. Decision #8 in `docs/TASK_PLAN.md`.

**How it works.** `[report] year_basis` in `config.toml` (`written_year` / `calendar`) drives steps 7, 11 and 12; to
reverse, set `calendar`, rerun steps 5, 6, 7, 11, 12 and refresh. `year_basis_status` is the text of `Year Basis Flag`.
The forecasting models still run on the whole book; only what the page shows changes.

| 2024 | Profitability | Path to Profitability | Pricing pages "Today" | Forecast "last year" |
|---|---|---|---|---|
| Premium | 358.9M | 358.9M | 358.9M | 358.9M |
| Claims | 255.0M | 255.0M | 255.0M | 255.0M |
| Combined ratio | 85.9% | 85.9% | 85.9% | 85.9% (loss ratio 71.0%) |

**Forecast page.** Each policy sits in the month it starts (a start outside its policy year goes to that year's first
or last month), so the 12 months add up to the policy-year figures. Forecast 2025: 382.9M premium, 385.6M claims.

**Read with care**
- *Superseded by §9 for 2024's claims (now one estimate on all pages).*
- **2024's loss ratio is model-based; 2025's is experience-based.** The jump from 71.0% to 100.7% is at least partly that
  difference, not a forecast of worsening. Over 2024 itself, actual claims on these policies were 152.4M against 122.4M
  from the model (+25%), so the 50.6M 2024 profit may be overstated. In year, actual claims give 103.3%; 2022 shows
  that view can run 16 points above whole-policy, so the gap cannot be fully separated from timing yet.
- **The continuing premium has no claims yet:** the data stops on 18 Jan 2025, so 2024 cannot be measured on actual claims.
- **Management Alerts stay at 172.5M** (`Alerts Basis Label`): they judge actual results, so they follow the same
  policies only inside the year, and year against year stays like for like.
- **Claims detail cards** (Bonus-Malus, UW Controls, Claims Mgmt) are 2024 policies' reported claims so far: 159.5M, not
  the 255.0M expected.

**Desktop steps.** Put `Year Basis Flag` in a banner card on every page and `Alerts Basis Label` on the alerts panel.

## 9. Alert to price: decided for the demo, needs manager review (2026-11-04)

The manager opens a flagged segment, drills through to Optimal Price, and sees **Today** (exactly the alert), **If we do
nothing**, and **Recommended**. All of it runs on one expected-claims figure. Every choice below was made for the demo and
is flagged on screen (`Alert Period`, the card, `Manager Review Flag`). It replaces the severity rules in
`docs/ALERTS_METHOD.md` §2 for the demo.

| Decision | What was chosen | Why | To reverse |
|---|---|---|---|
| Alert flags | **Current**: reliable combined ratio above target. **Future**: expected combined ratio above target and the segment not clearly improving. Both = Critical, one = Warning. A small book (reliability below 0.30) can only be a Warning, and only on its own results | The manager's two cases | Measures in folder 12 |
| Expected claims | Reliable loss ratio (actual results blended toward the class by credibility, as alerts did) × trend factor | Model claims contradicted the alert on 9 of 13 flagged segments | `expected_claims = "model"` |
| Trend factor | **1.039**: the forecast's 1.200 less its average error on 9 past forecast dates (+13.5 pts), recalculated every run | The raw forecast's error barely beat "no change" (16.4 vs 18.3 pts) and got the direction right 5 of 9 times. Corrected error: 12.3 pts, but that correction is measured on the same 9 dates, so it flatters the result | Step 07 |
| Price rule | No increase if under target; otherwise the smallest increase reaching the target; if unreachable, the highest the retention floors allow, showing the remaining gap | "Highest profit" always ran to a limit instead of aiming at the target | `selection_rule = "max_profit"` |
| 2024 on Profitability, Path, Forecast and pricing Today | Same estimate: whole premium × reliable loss ratio | One story across pages | `expected_claims = "model"` |
| Card's riyal amounts | All three columns on the alert's book (172.5M), so they compare and Today matches the alert exactly | The whole-policy book (358.9M) has no actual claims yet | Card measures |

**Result, base customer reaction**

| | Today (whole book) | If we do nothing (renewal book) | Recommended (+9.0% average) |
|---|---|---|---|
| Premium | 358.9M | 323.0M | 333.7M |
| Combined ratio | 103.2% | 106.7% | 97.3% |
| Underwriting result | −11.6M | −21.7M | +9.0M |
| Customers kept | 100% | 90% | 85.1% |

Forecast 2025: 382.9M premium, loss ratio 91.8%, underwriting result −25.7M. Alerts: 3 Critical, 10 Warning.

**Limits to say out loud**
- All 13 alerts are "Current and future": the trend factor is one portfolio number, so the future flag barely separates segments yet. There is no segment-level forecast.
- Only 7 of 22 segments reach the target within the retention floors, and the portfolio stays above 95%.
- The 90% renewal and the price sensitivity are still assumptions, and they decide where the floors stop the price.
- The target for the price is fixed at 95% in the optimizer; the report's target slider does not reprice.
- The Safe Zone map's "Today" cell is the 0% / 0% scenario, so it shows next year's expected ratio, not today's.
- 2024 reliable results come from claims so far with no allowance for late-reported claims; 2022 shows the in-year view can run 16 points above the whole-policy result.

**Desktop steps.** Add `POLICY_TYPE_DESC` to Optimal Price's drill-through fields, add a card with `Manager Review Flag` on the pages without one, and save.
