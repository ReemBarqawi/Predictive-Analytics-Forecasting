# Measure replacement map

> Built 2026-11-04 from the model and the report as last saved in Desktop (11:09). **Anything changed in Desktop after that save is not reflected here.** Nothing has been changed or deleted yet.

**Result:** 391 measures → 262. 50 duplicates merge into a confirmed measure, `Credible Combined Ratio` is removed by decision, and 78 that nothing uses are removed.

## The order

1. **You, in Desktop:** do Steps 1 and 2 below, then save.
2. **Claude:** re-reads the saved report and confirms no visual still uses an old name, then rewrites the formulas in Step 3.
3. **Claude:** deletes the measures in Step 4, in small batches, checking the figures after each one.
4. **You:** open each page and try the bookmarks.

Deleting first would break the visuals, so the order matters. The old names are all still valid until Step 4.

## The confirmed measures

| Concept | Confirmed measure | Replaces |
|---|---|---|
| Premium | `Written Premium` | `Total Earned Premium`, `What We Charge` |
| Premium | `Today Premium` | `Cur Premium`, `CY Earned Premium`, `PTP Baseline Premium` |
| Premium | `In-Year Premium` | `Alert Earned Premium` |
| Premium | `Rec Premium` | `Card Rec Premium` |
| Claims | `Ultimate Claims` | `Total Ultimate Claims`, `CM Segment Ultimate`, `What It Costs Us` |
| Claims | `Today Claims` | `Cur Claims`, `CY Claims` |
| Claims | `Reported Claims` | `CM Total Ultimate` |
| Claims | `Paid Claims` | `CM Total Paid` |
| Claims | `Rec Claims` | `Card Rec Claims` |
| Expenses | `Expenses` | `Total Expenses` |
| Expenses | `Commission` | `Total Commission` |
| Expenses | `Admin Expense` | `Total Admin Expense` |
| Expenses | `Today Expenses` | `Cur Expenses` |
| Expenses | `Rec Expenses` | `Card Rec Expenses` |
| Result and margin | `UW Result` | `Total UW Profit` |
| Result and margin | `Today Result` | `Cur Profit` |
| Result and margin | `UW Margin` | `Profit Margin`, `Raw Margin` |
| Combined ratio | `Today Combined Ratio` | `PTP Current CR`, `Cur Combined Ratio` |
| Combined ratio | `Combined Ratio` | `Raw Combined Ratio` |
| Combined ratio | `In-Year Combined Ratio` | `Alert Combined Ratio` |
| Combined ratio | `Reliable Combined Ratio` | `Alert Credible Combined Ratio` |
| Target | `Target Combined Ratio Value` | `PTP Target CR`, `Alert Target CR`, `AR Target CR`, `Glide Target Line`, `Rec Target CR` |
| Claim count | `Ultimate Claim Count` | `Claim Count`, `BM Claims This Segment`, `CM Segment Claims`, `UW Claims In Segment` |
| Severity and frequency | `Severity` | `UW Current Severity`, `UW Segment Avg Severity` |
| Severity and frequency | `Frequency` | `BM Segment Frequency` |
| Lever amounts | `PTP Repricing SAR` | `We Recover SAR` |
| Lever amounts | `PTP BonusMalus SAR` | `BM We Recover SAR` |
| Lever amounts | `PTP UW Controls SAR` | `UW We Recover SAR` |
| Lever amounts | `PTP Claims Mgmt SAR` | `CM We Recover SAR` |
| Lever amounts | `PTP Repricing CR Pts` | `PTP Repricing Raw` |
| Lever amounts | `PTP BonusMalus CR Pts` | `PTP BonusMalus Raw` |
| Lever amounts | `PTP UW Controls CR Pts` | `PTP UW Controls Raw` |
| Lever amounts | `PTP Claims Mgmt CR Pts` | `PTP Claims Mgmt Raw` |
| Retention floor | `Rec Retention Floor` | `Curve Floor` |

## Step 1: Move the lever tables' rows (Desktop)

Do this first. Base measures don't follow the lever table's own columns: on those rows `Written Premium` shows 358.9M on every row. On the dimension columns all 22 segments match exactly.

In each visual below, replace `fact_lever_allocation[ZONE]` with `dim_zone_summary[ZONE]`, `[CLASS_DESC]` with `dim_business_class[CLASS_DESC]`, and `[POLICY_TYPE_DESC]` with `dim_product[POLICY_TYPE_DESC]`.

| Page | Visual | Change |
|---|---|---|
| Risk-Based | Table "Segment-by-segment pricing gap" | Zone, class and policy type |
| Bonus-Malus | Table | Zone and class |
| Bonus-Malus | Bar chart "Claim frequency by segment" | Zone |
| uw (UW Controls) | Table | Class only (zone is already correct) |
| Page 1 (Claims Mgmt) | Table | Zone and class |

Also move any filter on `fact_lever_allocation[ZONE]` or `[CLASS_DESC]` in these visuals' filter panes. The filters on `baseline_earned_premium` and `pricing_adequacy` won't reach the new measures. After the swap, check the Risk-Based table still shows the rows you expect.

## Step 2: Swap the fields (Desktop)

Listed by page. **Where** says where to find the old field. *Position* is the visual's x, y on the page, from the top-left corner.

### Profitability

| ✓ | Visual | Position | Old | New | Where |
|---|---|---|---|---|---|
| ☐ | Card | 396, 208 | `Total Earned Premium` | `Written Premium` | Fields |
| ☐ | Card | 96, 214 | `Total UW Profit` | `UW Result` | Fields, Conditional format |
| ☐ | Card | 698, 214 | `Total Ultimate Claims` | `Ultimate Claims` | Fields |
| ☐ | Card | 995, 214 | `Total Expenses` | `Expenses` | Fields |
| ☐ | Column chart "Where the book earns and loses." | 1276, 215 | `Total UW Profit` | `UW Result` | Fields |
| ☐ | Deneb "Underwriting Performance by Segment" | 96, 395 | `Profit Margin` | `UW Margin` | Fields |
| ☐ | Deneb "Underwriting Performance by Segment" | 96, 395 | `Total Earned Premium` | `Written Premium` | Fields |
| ☐ | Deneb "Underwriting Performance by Segment" | 96, 395 | `Total UW Profit` | `UW Result` | Fields |

### Path to Profitability

| ✓ | Visual | Position | Old | New | Where |
|---|---|---|---|---|---|
| ☐ | Card "Combined ratio today" | 105, 196 | `PTP Current CR` | `Today Combined Ratio` | Fields |
| ☐ | Card | 448, 197 | `PTP Target CR` | `Target Combined Ratio Value` | Fields |

### Optimal Price

| ✓ | Visual | Position | Old | New | Where |
|---|---|---|---|---|---|
| ☐ | Line chart "Profit vs. customer retention by price change" | 743, 239 | `Curve Floor` | `Rec Retention Floor` | Fields |

### Page 3 (price curve)

| ✓ | Visual | Position | Old | New | Where |
|---|---|---|---|---|---|
| ☐ | Deneb | 2, 3 | `Curve Floor` | `Rec Retention Floor` | Fields |

### Risk-Based

| ✓ | Visual | Position | Old | New | Where |
|---|---|---|---|---|---|
| ☐ | Table "Segment-by-segment pricing gap‎‎ ‎‎" | 101, 468 | `We Recover SAR` | `PTP Repricing SAR` | Fields |
| ☐ | Table "Segment-by-segment pricing gap‎‎ ‎‎" | 101, 468 | `What It Costs Us` | `Ultimate Claims` | Fields |
| ☐ | Table "Segment-by-segment pricing gap‎‎ ‎‎" | 101, 468 | `What We Charge` | `Written Premium` | Fields |

### Bonus-Malus

| ✓ | Visual | Position | Old | New | Where |
|---|---|---|---|---|---|
| ☐ | Table | 102, 477 | `BM Claims This Segment` | `Ultimate Claim Count` | Fields |
| ☐ | Table | 102, 477 | `BM Segment Frequency` | `Frequency` | Fields |
| ☐ | Table | 102, 477 | `BM We Recover SAR` | `PTP BonusMalus SAR` | Fields |
| ☐ | Bar chart "Claim frequency by segment" | 1080, 641 | `BM Segment Frequency` | `Frequency` | Fields |

### uw (UW Controls)

| ✓ | Visual | Position | Old | New | Where |
|---|---|---|---|---|---|
| ☐ | Table | 74, 483 | `UW Claims In Segment` | `Ultimate Claim Count` | Fields, Filters pane |
| ☐ | Table | 74, 483 | `UW Current Severity` | `Severity` | Fields, Filters pane |
| ☐ | Table | 74, 483 | `UW We Recover SAR` | `PTP UW Controls SAR` | Fields, Filters pane |

### Page 1 (Claims Mgmt)

| ✓ | Visual | Position | Old | New | Where |
|---|---|---|---|---|---|
| ☐ | Table | 0, 439 | `CM Segment Claims` | `Ultimate Claim Count` | Fields |
| ☐ | Table | 0, 439 | `CM Segment Ultimate` | `Ultimate Claims` | Fields |
| ☐ | Table | 0, 439 | `CM We Recover SAR` | `PTP Claims Mgmt SAR` | Fields |

**Deneb visuals:** after swapping a field, use **Rename for this visual** to give it the old name, or the chart loses its data because the spec reads fields by name.

**Bookmarks:** the report's 27 bookmarks keep their own copy of visual state. After the swaps, go to each bookmark for the pages you changed, check it, and choose **Update** if it looks right.

### Step 2b: Remove `Credible Combined Ratio` from the Deneb chart

**One visual changes:** Profitability, Deneb "Underwriting Performance by Segment" (position 96, 395). It is the only visual using this measure, and it never displays it: the cards already show `Combined Ratio`. The chart only uses it to check the field exists and to decide which cards to draw, so **no figure on the page changes**.

Do these in order. Removing the field first would blank the chart with a "Rows arrived without…" message.

1. In **Edit** on the visual, change the spec:

| Part of the spec | Old | New |
|---|---|---|
| `hasFields` signal | `isDefined(data('dataset')[0]['Credible Combined Ratio'])` | `isDefined(data('dataset')[0]['Combined Ratio'])` |
| Warning message | `Rows arrived without Profit Margin, Credible Combined Ratio or Perf Status Color` | `Rows arrived without Profit Margin, Combined Ratio or Perf Status Color` |
| `cards` filter | `isValid(datum['Credible Combined Ratio'])` | `isValid(datum['Credible Margin'])` |
| Description at the top | `Combined Ratio, Credible Margin, Credible Combined Ratio, Total Earned Premium` | `Combined Ratio, Credible Margin, Total Earned Premium` |

2. Remove `Credible Combined Ratio` from the visual's **Values** well.

3. Check the page shows **15 cards: 12 with figures and 3 grey "no business"** (Northern Commercial, Northern Rent a Car, Southern Rent a Car).

The filter uses `Credible Margin`, not `Combined Ratio`, on purpose: `Combined Ratio` is blank for those 3 cells, so they would disappear and leave gaps in the grid.

### Numbers that will change on purpose

| Measure | Today | After | Why |
|---|---|---|---|
| `BM Segment Frequency` → `Frequency`, on the Bonus-Malus table and zone bar chart | Overall 8.09 claims per policy year | Overall 0.145 | The old one averages each segment's ratio, so small books count as much as large ones |
| `What It Costs Us` → `Ultimate Claims` | 317,170,358 | 317,175,914 | The old one rebuilds claims from exposure and loses 5,556 SAR in one segment |

### Bonus-Malus actions that will change

`BM Action` follows frequency, so these 4 of 12 table rows get a different label. The other 8 keep theirs.

| Segment | Frequency now | After | Action now | After |
|---|---|---|---|---|
| Central, Motor Private | 0.195 | 0.124 | Monitor | On target |
| Eastern, Motor Private | 0.319 | 0.154 | Surcharge at renewal | Monitor |
| Southern, Motor Private | 0.172 | 0.093 | Monitor | Reward: discount eligible |
| Western, Motor Private | 0.315 | 0.132 | Surcharge at renewal | On target |

## Step 3: Formula rewrites (Claude)

No Desktop work. 40 measures that stay use an old name inside their formula. Each reference is replaced with the confirmed measure; the logic is unchanged.

| Measure | Replace |
|---|---|
| `Alert Expense Ratio` | `Alert Earned Premium` → `In-Year Premium` |
| `Alert Issue` | `Alert Combined Ratio` → `In-Year Combined Ratio`, `Alert Earned Premium` → `In-Year Premium`, `Alert Target CR` → `Target Combined Ratio Value` |
| `Alert Level` | `Alert Combined Ratio` → `In-Year Combined Ratio`, `Alert Target CR` → `Target Combined Ratio Value` |
| `Alert Loss Ratio` | `Alert Earned Premium` → `In-Year Premium` |
| `Alert Period` | `Alert Target CR` → `Target Combined Ratio Value` |
| `Alert SAR Above Target` | `Alert Target CR` → `Target Combined Ratio Value` |
| `BM Frequency vs Average` | `BM Segment Frequency` → `Frequency` |
| `CM IBNR %` | `CM Total Ultimate` → `Reported Claims` |
| `CM Unsettled Amount` | `CM Total Paid` → `Paid Claims`, `CM Total Ultimate` → `Reported Claims` |
| `Class Prior Margin` | `Raw Margin` → `UW Margin` |
| `Credibility Z` | `Claim Count` → `Ultimate Claim Count` |
| `Credible Margin` | `Raw Margin` → `UW Margin` |
| `Current Flag` | `Alert Credible Combined Ratio` → `Reliable Combined Ratio`, `Alert Target CR` → `Target Combined Ratio Value` |
| `Curve Profit Delta` | `Cur Profit` → `Today Result` |
| `Expected Loss Ratio` | `Alert Credible Combined Ratio` → `Reliable Combined Ratio` |
| `Future Flag` | `Alert Target CR` → `Target Combined Ratio Value` |
| `Glide After Actions CR` | `PTP Current CR` → `Today Combined Ratio` |
| `PTP BonusMalus Active Raw` | `PTP BonusMalus Raw` → `PTP BonusMalus CR Pts` |
| `PTP BonusMalus CR Pts` | `PTP Baseline Premium` → `Today Premium` |
| `PTP Claims Mgmt Active Raw` | `PTP Claims Mgmt Raw` → `PTP Claims Mgmt CR Pts` |
| `PTP Claims Mgmt CR Pts` | `PTP Baseline Premium` → `Today Premium` |
| `PTP Distribution Raw` | `PTP Baseline Premium` → `Today Premium` |
| `PTP Gap CR` | `PTP Current CR` → `Today Combined Ratio`, `PTP Target CR` → `Target Combined Ratio Value` |
| `PTP Glide CR` | `PTP Current CR` → `Today Combined Ratio` |
| `PTP Lever Potential Pts` | `PTP BonusMalus Raw` → `PTP BonusMalus CR Pts`, `PTP Claims Mgmt Raw` → `PTP Claims Mgmt CR Pts`, `PTP Repricing Raw` → `PTP Repricing CR Pts`, `PTP UW Controls Raw` → `PTP UW Controls CR Pts` |
| `PTP Repricing Active Raw` | `PTP Repricing Raw` → `PTP Repricing CR Pts` |
| `PTP Repricing CR Pts` | `PTP Baseline Premium` → `Today Premium` |
| `PTP Scale Factor` | `PTP Current CR` → `Today Combined Ratio`, `PTP Target CR` → `Target Combined Ratio Value` |
| `PTP UW Controls Active Raw` | `PTP UW Controls Raw` → `PTP UW Controls CR Pts` |
| `PTP UW Controls CR Pts` | `PTP Baseline Premium` → `Today Premium` |
| `Perf Data Tag` | `Claim Count` → `Ultimate Claim Count` |
| `Perf Status` | `Total Earned Premium` → `Written Premium` |
| `Rec KPI Card HTML` | `Alert Combined Ratio` → `In-Year Combined Ratio`, `Alert Earned Premium` → `In-Year Premium`, `Rec Target CR` → `Target Combined Ratio Value` |
| `Rec Stopped By` | `Rec Target CR` → `Target Combined Ratio Value` |
| `Safe Zone Card HTML Body` | `Cur Claims` → `Today Claims`, `Cur Combined Ratio` → `Today Combined Ratio`, `Cur Expenses` → `Today Expenses`, `Cur Premium` → `Today Premium`, `Cur Profit` → `Today Result` |
| `UW Severity vs Average` | `UW Segment Avg Severity` → `Severity` |
| `UW Target Severity` | `UW Segment Avg Severity` → `Severity` |
| `WI Bridge Value` | `Cur Profit` → `Today Result` |
| `WI Cur Loss Ratio` | `Cur Claims` → `Today Claims`, `Cur Premium` → `Today Premium` |
| `WI Impact HTML Body` | `Cur Claims` → `Today Claims`, `Cur Combined Ratio` → `Today Combined Ratio`, `Cur Expenses` → `Today Expenses`, `Cur Premium` → `Today Premium`, `Cur Profit` → `Today Result` |

## Step 4: Delete (Claude)

### Merged duplicates

The 50 names in the "Replaces" column above.

### Removed by decision

`Credible Combined Ratio` (Step 2b). `Class Prior Combined Ratio` goes with it: nothing else uses it, so it is in the list below.

### Nothing uses these (78)

No visual uses them, directly or through another measure, as of the 11:09 save. Tell me if you want any kept. The slider tables themselves are not deleted.

| Folder | Measures |
|---|---|
| (no folder: slider values) | `Best Scenario Toggle Value`, `Claim frequency change Value`, `Claim severity change Value`, `Expense change Value`, `Exposure growth Value`, `Premium Change % Value` |
| 00 Report Calendar | `CY Loss Ratio`, `FY Claims`, `FY Loss Ratio`, `FY Premium` |
| 02 Credibility | `Class Prior Combined Ratio`, `Class Prior Expected Claims Ratio`, `Class Prior Loss Ratio`, `Credible Expected Claims`, `Credible Loss Ratio`, `Raw Expected Claims`, `Raw Loss Ratio` |
| 03 Forecast | `Chart Actual Claims`, `Chart Actual LR`, `Chart Actual Premium`, `Chart Forecast Claims`, `Chart Forecast Claims Adjusted`, `Chart Forecast LR Adjusted`, `Chart Forecast Premium`, `Chart Forecast Premium Adjusted` |
| 03 Forecast\Chart gaps | `Chart Gap Green Anchor`, `Chart Gap Green High`, `Chart Gap Green Low`, `Chart Gap Red Anchor`, `Chart Gap Red High`, `Chart Gap Red Low`, `Chart Off Target` |
| 04 Scenario Simulator | `Sim Avg Severity`, `Sim Baseline Admin Expense`, `Sim Baseline Claim Count`, `Sim Baseline Claims`, `Sim Baseline Commission`, `Sim Baseline Exposure`, `Sim Baseline Premium`, `Sim Best Admin Expense`, `Sim Best Commission`, `Sim Best Premium`, `Sim Scenario Admin Expense`, `Sim Scenario Claim Count`, `Sim Scenario Claims`, `Sim Scenario Combined Ratio`, `Sim Scenario Commission`, `Sim Scenario Expense Ratio`, `Sim Scenario Exposure`, `Sim Scenario Loss Ratio`, `Sim Scenario Premium`, `Sim Scenario Total Expenses`, `Sim Scenario UW Profit` |
| 04 What-If\Bridge | `WI Waterfall Value` |
| 05 Safe Zone | `BE Combined Ratio`, `BE Combined Ratio Display`, `BE Loss Ratio`, `BE Margin of Safety`, `BE Max Safe Discount`, `BE UW Profit`, `Base Premium`, `Break Even Premium`, `Heatmap Impact HTML`, `Scenario Breakdown HTML` |
| 10 Path to Profitability | `AR Lever Cards HTML`, `AR Smart Waterfall Bars`, `PTP Recovered SAR Active` |
| 11 Price Recommendation | `Breadcrumb` |
| 11 Price Recommendation\Current | `Cur Retained Exposure` |
| 11 Price Recommendation\Recommended | `Rec Bridge Value`, `Rec Change High`, `Rec Change Low`, `Rec KPI Card HTML Old`, `Rec Loss Ratio`, `Rec Min Policies`, `Rec Range Text`, `Rec Retained Policies` |
| 12 Management Alerts | `Alert Model Combined Ratio` |

20 of the deleted names are also held in bookmark state, which is why the bookmark check above matters.

## Kept: these look like duplicates but are different numbers

| Measure | Value now | What it is |
|---|---|---|
| `Written Premium` | 358.9M | **The premium.** Policies written in the year; follows the year selected |
| `Today Premium` | 358.9M | Same number locked to the current year, for the price card and alerts. Hide it, don't put it on visuals |
| `In-Year Premium` | 172.5M | Part earned so far, the alert's supporting line |
| `FC Total Premium` | 382.9M | Forecast, all policies expected next year |
| `Premium Last Year` | 358.9M at 12M | Same months as the forecast window, so it differs at 3M or 6M |
| `Card NoAction Premium` | 323.0M | Next year if nothing changes: 2024 policies that renew |
| `Rec Premium` | Depends on segment | Premium at the recommended price |
| `Curve Premium`, `SZ Premium`, `WI Scn Premium` | Depends on slider | Premium at a chosen price, one per page's slider |
| `Pure Premium` | Claims per policy year | A different concept: the cost of risk |

The same split applies to claims, expenses and result: `FC …`, `Rec …`, `Curve …`, `SZ …` and `WI Scn …` are forecast or scenario figures, so they keep their names.

## Pre-delete check (2026-11-04)

Read-only: nothing was changed. **Result: not safe to delete yet.** Run this check again after the Desktop steps are saved.

| Check | Result | Status |
|---|---|---|
| Measures in the live model | 391, all valid, none added or removed since this map was built | OK |
| Used by a calculated column, table or other model object | `chart Value`, used by `chart[chartname]`; now kept | Fixed in this map |
| Removed measures a kept measure needs | None | OK |
| Removed measures on a visual | None | OK |
| Old values vs new, in total and by zone, class and policy type | Match, apart from the two corrections in Step 2 | OK |
| Old names still on a saved visual | 21: the report was last saved at 11:09, before the swaps | **Blocks delete** |
| Merged names inside formulas | 40 measures to rewrite (Step 3) | **Blocks delete** |
| Held in bookmarks | 20 candidates, including 5 no visual uses: `AR Smart Waterfall Bars`, `Chart Actual Premium`, `Chart Forecast Premium Adjusted`, `Credible Loss Ratio`, `Scenario Breakdown HTML` | Update or test the bookmarks first |

Not compared by value, because they are proven by their formula: pointers such as `Total Earned Premium` and measures with the same formula as their replacement.

**Card and line chart formatting:** the 4 Profitability cards, 2 Path to Profitability cards and the Optimal Price line chart store formatting against the old measure name. After swapping, check the format hasn't reset.

## Needs your decision

1. **`Credible Margin` has the same double blending and is still shown.** It is in the Profitability column chart's formatting, fills the Deneb cards' "Expected margin" tooltip, and sits in a hidden table. Southern, Motor Private is +1.3% on `UW Margin` but -0.8% on `Credible Margin`. Not changed here. If it goes too, the Deneb `cards` filter in Step 2b needs another field, or the 3 grey cards disappear.
2. **`Curve`, `SZ` and `WI Scn` measures.** They read the same scenario table with a different slider. They could merge if the pages shared one price slider, but that changes how the pages work, so they stay for now.

## Also noticed

- **Claim frequency on Motor Rent a Car** reaches 38.6 claims per policy year (Western, Comprehensive), against 0.145 overall. That points to very low exposure in those rows. Worth checking, because it feeds the Bonus-Malus lever. The merge doesn't cause or change this.
- **`CM IBNR %` and `CM Segment IBNR %`** read 0% because no IBNR is stored per claim. `Claims Still To Come` shows 157.7M. They stay until Phase 3, as METRIC_STANDARD §10 says.

## Done: 2026-11-04

**391 measures → 278.** 73 unused and 40 merged duplicates were deleted from the open model, and 44 formulas now use the confirmed measures. Every remaining measure is valid. The numbers above were built before these decisions, so where they differ, this section applies.

Every measure was compared before and after in 5 contexts (total, two segments, one zone, year 2023). Only the changes below moved:

| Measure | Change | Why |
|---|---|---|
| `BM Frequency vs Average`, `BM Action` | Follow `Frequency` | Accepted by the user: 4 Motor Private labels change |
| `UW Current Severity`, `UW Severity vs Average`, `UW Severity vs Average %`, `UW Target Severity` | Follow a selected year | The uw page has no year slicer, so its figures don't move |

**Kept on purpose**

| Measures | Why |
|---|---|
| `We Recover SAR`, `What It Costs Us`, `What We Charge`, `UW Claims In Segment`, `UW Current Severity`, `UW We Recover SAR`, `BM Claims This Segment`, `BM Segment Frequency`, `BM We Recover SAR` | Hidden in folder **99 Kept for bookmarks**. The user chose not to change bookmarks, and 6 bookmarks restore their saved filters and sort |
| `Total UW Profit`, `Credible Combined Ratio`, `Class Prior Combined Ratio` | Still used in the 15:00 save. Delete after the card formatting and Deneb edits are saved |
| `AR Smart Waterfall Bars`, `Chart Actual Premium`, `Chart Forecast Premium Adjusted`, `Chart Forecast Premium` | In bookmarks that restore data, or needed by one that is |
| `CM Action`, `CM IBNR %`, `CM Inaction Cost`, `CM Open Claims`, `CM Segment IBNR %`, `CM Segment Open %`, `CM Total IBNR`, `CM Unsettled Amount` | Page 1 (Claims Mgmt) isn't in the 15:00 save, so nothing uses them, but they were not on the approved list |

**Backup:** `data/real/backups/` holds the 15:00 report and every measure's formula (git-ignored).

## Next delete list

Not deleted yet. Page 1 (Claims Mgmt) was removed on purpose, per the user.

| Measures | Why |
|---|---|
| `CM Action`, `CM IBNR %`, `CM Inaction Cost`, `CM Open Claims`, `CM Segment IBNR %`, `CM Segment Open %`, `CM Total IBNR`, `CM Unsettled Amount` | Only Page 1 used them |
| `Credible Combined Ratio`, `Class Prior Combined Ratio` | Nothing uses them in the 15:29 save |

**Claims Mgmt measures that must stay:** `PTP Claims Mgmt SAR`, `PTP Claims Mgmt CR Pts`, `PTP Claims Mgmt Active`, `PTP Claims Mgmt Active Raw`, `Toggle Claims Mgmt Value`. They feed the Claims Mgmt lever on Path to Profitability.

**Done, 2026-11-04:** all 10 on this list were deleted, leaving **268 measures**, all valid, with no values changed. Backup: `measures_last_10_2026-11-04.json` and the 15:29 report in `data/real/backups/`. `Credible Combined Ratio` is still named in the `open filter pro` and `close filter pro` bookmarks, which don't apply saved data. `Total UW Profit` stays until the UW profit card's formatting is changed.
