# Semantic model cleanup — unused objects

> **Status (2026-10-26): deletions and folder reorganisation done** in `clean model.pbix` (Desktop copy of the master report),
> from the live model in Power BI Desktop for Report Server. Backup of the file before the deletes:
> `clean model_backup_before_delete.pbix`. Still open: see [What's still open](#whats-still-open).
>
> First analysis 2026-10-21 on `predicting_insurance_DSS4.pbix`; re-checked 2026-10-26 on the open file before deleting.

**Method:** an object counts as *used* if a visual, visual filter, page/report filter, conditional format or bookmark references it,
or if a used measure, calculated table or calculated column depends on it (followed through every chain via `INFO.CALCDEPENDENCY`).
The report side is read from `Report/Layout` inside the .pbix as last saved, so unsaved report edits are not included.

## Result

| | Before | After |
|---|---|---|
| Measures | 466 | 242 (all used) |
| Tables, incl. hidden auto-date tables | 73 | 58 |
| Relationships | 54 | 42 |

Deleted in three batches, each checked first so that nothing outside the batch referred to it:

1. **113 measures** — whole folders with nothing in use: `02 Pricing`, `04 Profitability Surface`, `05 Scenarios`, `06 Earned Premium`,
   `08 Development`, `09 Model Performance`, `10 Policy`, `13 Alerts`, `13 Optimizer`, `14 kpi forecast`, `31 What-If`.
2. **111 measures** — unused measures scattered through folders that also hold used measures.
3. **14 tables** — the 13 below plus the `Claims inflation` what-if parameter (its only consumer was `WI Inflation`).

After each batch no measure, column or partition was in an error state, and the final re-check shows zero unused measures and tables.

### Differences from the first analysis

- `Total Paid` was still referenced, but only by a leftover number-format selector on a chart it is no longer on. Deleted in batch 2.
- 14 measures had become unused since the first analysis and were deleted too: `Repricing Story`, `Sim Scenario Status`,
  `Sim Status Card HTML`, `Sim Status Explanation`, `Bubble Cred Colour`, `Adequacy Caveat`, `FC Actual Claims`, `FC Actual Premium`,
  `FC Actual Loss Ratio`, `FC Actual UW Profit`, `FC Gap Claims Pct`, `FC Gap Premium Pct`, `FC Gap LR Pts`, `FC Gap UW Profit`.
- **`Full Cred Standard` is in use** (the `Credibility Z` measure reads it), so it is not a leftover and was kept.

## Display folders (new layout)

All 224 measures on `measures table` are filed by report area. Measures on their own tables (the slicer `… Value` measures such as
the five toggles and `Target Combined Ratio`, the three `Waterfall …` measures, `chart Value`) stay on those tables.

| Folder | # | Contents | Page(s) |
|---|---|---|---|
| 01 Portfolio KPIs | 11 | Premium, claims, expenses, ratios, UW profit | Profitability, used across pages |
| 02 Credibility | 14 | `Raw …`, `Class Prior …`, `Credible …`, `Credibility Z`, `Confidence Concept Card` | Profitability, Safe Zone |
| 03 Forecast | 21 | `Chart …`, `FC …`, `Growth Label` | Forecast |
| 04 Scenario Simulator | 23 | `Sim Baseline / Scenario / Current …`, `Sim Best Premium / Commission / Admin Expense`, `Scenario Impact HTML` | Scenario Simulator, Safe Zone |
| 05 Safe Zone | 10 | `Break Even Premium`, `BE …`, `Base Premium`, `Heatmap Impact HTML`, `Scenario Breakdown HTML` | Safe Zone |
| 06 Repricing | 14 | `Pricing Gap %`, `Rate Increase Needed`, `Repricing …`, `What We Charge`, `What It Costs Us`, `We Recover SAR`, `PTP Repricing SAR / CR Pts` | Risk-Based |
| 07 Bonus-Malus | 21 | All `BM …`, `PTP BonusMalus SAR / CR Pts` | Bonus-Malus |
| 08 UW Controls | 19 | All `UW …`, `PTP UW Controls SAR / CR Pts` | uw, Page 1 |
| 09 Claims Mgmt | 15 | All `CM …`, `PTP Claims Mgmt SAR / CR Pts` | Page 1 |
| 10 Path to Profitability | 30 | `Glide …`, PTP glide and lever-switch measures, `PTP Current / Target / Gap CR`, `PTP Total Recovered SAR`, `PTP Distribution SAR`, `AR …` | Path to Profitability |
| 11 Price Recommendation | 46 | `Breadcrumb`; sub-folders `Current` (7 `Cur …`), `Recommended` (21 `Rec …`), `Curve` (17 `Curve …`) | Page 2, Page 3 |

New measures go in the folder of the page they serve. A new report area gets the next number (`12 …`).

## What's still open

- [ ] **Save** the file in Desktop, click through every page, then copy it back over the master `predicting_insurance_DSS4_RS.pbix`.
- [ ] **Turn off Auto date/time** (File → Options → Current file → Data load). Removes the remaining 10 hidden `LocalDateTable_…` tables.
- [ ] **Delete the `Errors in fact_alerts` query.**
- [x] **`fact_alerts` loads all its columns** (2026-10-26: `Columns=18`, `QuoteStyle.Csv`, new `POLICY_YEAR`, `DETECTED_DATE`, `ALERT_RULE_ID`).
  Segment alerts now come from the new table `fact_alert_basis` (rolling 12 months of actual results, decided in DAX,
  `docs/ALERTS_METHOD.md`); `fact_alerts` keeps only the portfolio checks (17 columns). The Forecast page alert panel
  is a Deneb visual (`powerbi/deneb/management_alerts.vega.json`) and shows the action (CEO feedback #4).
- [ ] **`PTP Target CR` has no default value.**
- [ ] **Bookmarks** still reference 5 measures that no longer exist (`AR Current CR`, `AR Gap to Target`, `AR Projected CR`, `AR Total SAR Recovery`, `Management Alerts HTML`). Harmless, but those bookmarks may be stale.
- [ ] **Page names:** `Page 1`–`Page 4` and `uw` need proper names (renamed in Desktop).
- [ ] **Measure descriptions** are empty.
- [ ] **Publish to the test server** and check every page there.

## Record: what was deleted

### Tables (14)

| Table | Why it was unused |
|---|---|
| `fact_policy` | Policy-level fact; pages use fact_policy_pricing / fact_earned_premium |
| `dim_date` | Related to the facts, but no visual, slicer or measure used it (visuals use fact columns such as MONTH_DATE) |
| `fact_scenarios` | Old preset scenario grid (pre-optimizer) |
| `fact_kpi_forecast` | Old forecast KPI cards (flat run-rate); Forecast page uses fact_forecast_seasonal |
| `fact_timeseries` | Old forecast chart series; replaced by fact_forecast_seasonal |
| `fact_triangle_paid` | Development triangle; no page used it (Python still writes the CSV) |
| `fact_triangle_incurred` | Development triangle; no page used it (Python still writes the CSV) |
| `fact_dev_factors_paid` | Chain-ladder factors; no page used it (Python still uses the CSV) |
| `LeverRealization` | DAX table of horizon realization %; glide measures hold their own values |
| `LeverToggles` | DAX lever list; pages use ActionLevers + the 5 Toggle tables |
| `OptWaterfallSteps` | Waterfall axis for the old Optimizer measures |
| `PriceStep` | Calculated table for the old What-If measures |
| `RecMetric` | Calculated table for the old Rec Col / WI Col measures |
| `Claims inflation` | What-if parameter; only used by `WI Inflation` |

### Measures (224)

**(no folder):** `Selected Lever Value`, `Forecast Target Value`, `Claims inflation Value`, `Repricing Story`

**01 Portfolio KPIs:** `Policy Count`, `Total Exposure Years`, `Claim Frequency`, `Average Severity`

**02 Pricing (whole folder):** `Avg Pure Premium Per Year`, `Avg Charged Premium`, `Total Pure Premium`, `Total Charged Premium`, `Avg Expected Frequency`, `Avg Expected Severity`, `Policies Scored`, `Pricing Adequacy Ratio`, `Pricing Position`

**03 Safe Zone:** `Max Safe Discount`, `Premium Above Break-Even`, `Safe Zone Status`

**04 Profitability Surface (whole folder):** `Surface Combined Ratio`, `Surface UW Profit`, `Surface Margin`

**05 Scenarios (whole folder):** `Scenario Premium`, `Scenario Claim Cost`, `Scenario Loss Ratio`, `Scenario Combined Ratio`, `Scenario UW Profit`, `Scenario Margin`, `Baseline Premium`, `Baseline Claim Cost`, `Baseline Exposure`, `Scenario Exposure`, `Delta Premium`, `Delta Claim Cost`

**06 Earned Premium (whole folder):** `Monthly Earned Premium`

**07 Claims (whole folder):** `Total Paid`, `Total Incurred`, `Total Ultimate`, `Total Case Reserve`, `Total IBNR`, `Claim Count (fact_claim)`

**08 Development (whole folder):** `Cumulative Paid`, `Cumulative Incurred`

**09 Model Performance (whole folder):** `WAPE`, `Forecast Bias`, `MAE`, `RMSE`, `Predicted Total`, `Actual Total`

**10 Policy (whole folder):** `Total Gross Premium`, `Motor Policy Count`, `Cancelled Policy Count`

**11 Scenario Simulation:** `Sim Delta Premium`, `Sim Delta Claims`, `Sim Scenario Margin`, `Sim Delta UW Profit`, `Sim Scenario Status`, `Sim Current Loss Ratio`, `Sim Current Combined Ratio`

**12 Glide Path:** `Glide Gap Remaining`

**12 HTML:** `Sim Within Safe Zone`, `Sim Status Icon`, `Sim Status Color`, `Sim Status Bg`, `Sim Status Explanation`, `Sim Status Card HTML`, `Table Confidence Legend`

**13 Alerts (whole folder):** `Alert Count Open`, `Alert Count Action`, `Alert Count Critical`, `Alert Count Warning`, `Alert Count Info`, `Alert Header Text`

**13 Optimizer (whole folder):** `Opt Premium`, `Opt Claims`, `Opt Expenses`, `Opt Profit`, `Opt Profit Change`, `Opt Retained Exposure`, `Opt Baseline Exposure`, `Opt Target Combined Ratio`, `Opt Current Combined Ratio`, `Opt Assumption Label`, `Frontier Profit`, `Frontier Baseline Exposure`, `Opt Current Profit`, `Opt Retention`, `Opt Combined Ratio`, `Opt Premium Change`, `Opt Contraction Segments`, `Frontier Retention`, `Opt Contraction Warning`, `Opt Remaining Gap Pts`, `Opt Why Text`, `Opt Change Low Sens`, `Opt Change Base`, `Opt Change High Sens`, `Opt Range Text`, `Opt Waterfall Value`

**14 kpi forecast (whole folder):** `Fcst Value`, `Ref Value`, `Delta`, `Delta Label`, `Delta Colour`, `Actual`, `Forecast`, `PI Lower`, `PI Upper`, `Cell Confidence Colour`, `Bubble Cred Colour`, `Forecast Quarter Index`, `Forecast Adjusted`, `Monthly Loss Ratio`, `Monthly UW Margin`

**15 Break-even:** `Claim Count new`, `Base Cell Highlight`, `Default Growth Rate`, `Growth vs Default`

**16:** `Sim Best Claims`, `Sim Best UW Profit`, `Sim Best Total Expenses`, `Sim Best Loss Ratio`, `Sim Best Combined Ratio`, `Sim Best Margin`, `Sim Active Premium`, `Sim Active Claims`, `Sim Active Total Expenses`, `Sim Active UW Profit`, `Sim Active Loss Ratio`, `Sim Active Combined Ratio`, `Sim Active Margin`, `Sim Best Status`

**17 Action Recommender:** `PTP Gap SAR`, `PTP Distribution CR Pts`, `PTP After Actions CR`, `Total repricing impact1`, `Segments affected 1`, `Pure Premium Needed1`, `Adequacy Caveat`

**19 Bonus-Malus Detail:** `BM Claims Per Policy`, `BM Avg Premium Per Policy`, `BM Surcharge Revenue`, `BM Discount Cost`, `BM Net Premium Change`, `BM Frequency Status`, `BM Exposure This Segment`, `BM Story`

**20 UW Controls Detail:** `UW Portfolio Avg Severity`, `UW Claim Count By Type`, `UW Total Cost By Type`, `UW Open Claims Count`, `UW Closed Claims Count`, `UW IBNR Exposure`, `UW IBNR as % of Ultimate`, `UW Case Reserve`, `UW Open Claims %`, `UW High Severity %`, `UW Story`, `UW Total Claims Cost`, `UW Severity Reduction`

**21 Claims Mgmt Detail:** `CM Total Claims`, `CM Closed Claims`, `CM Open %`, `CM Settlement Rate`, `CM Total Case Reserve`, `CM Reserve %`, `CM Paid %`, `CM Avg Dev Months Open`, `CM Avg Dev Months Closed`, `CM Triangle Value`, `CM Story`

**22 Forecast Page:** `FC CI Lower`, `FC CI Upper`, `FC Actual Claims`, `FC Actual Premium`, `FC Actual Loss Ratio`, `FC Actual UW Profit`, `FC Gap Claims Pct`, `FC Gap Premium Pct`, `FC Gap LR Pts`, `FC Gap UW Profit`

**30 Recommendation:** `Rec Admin`, `Rec Commission`, `Cur Admin`, `Rec Price Card`, `Rec Badge`, `Rec Insight Text`, `Rec Basis Label`, `Rec Col Current`, `Rec Col Recommended`, `Rec Col Change`, `Rec Col Change Colour`, `Curve Total Cost`

**31 What-If (whole folder):** `WI Price`, `WI Inflation`, `WI Expense Shift`, `WI Premium`, `WI Claims Engine`, `WI Commission`, `WI Admin Engine`, `WI Retained Exposure`, `WI Claims`, `WI Expenses`, `WI Profit`, `WI Combined Ratio`, `WI Retention`, `WI Segments`, `WI Blocked Segments`, `WR Claims`, `WR Expenses`, `WR Profit`, `WR Combined Ratio`, `WC Claims`, `WC Expenses`, `WC Profit`, `WI Col Recommended`, `WI Col Mine`, `WI Col Difference`, `WI Warnings`, `WI Verdict`, `WI Verdict Colour`, `WI Bridge Value`, `WI Basis Label`
