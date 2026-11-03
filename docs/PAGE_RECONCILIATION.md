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
| 5 | Report: page filters | **Optimal Price** has hidden page filters (at the 2026-11-03 09:02 save: *Zone = Central, Class = Motor Rent a Car*). Safe Zone's were cleared in that save | Optimal Price shows one segment while the others show the portfolio | **To do in Desktop** (§4) |
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
