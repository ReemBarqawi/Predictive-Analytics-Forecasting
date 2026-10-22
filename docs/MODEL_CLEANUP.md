# Semantic model cleanup — unused objects

> Generated 2026-10-21 from the live model in Power BI Desktop plus the report definition saved in
> `predicting_insurance_DSS4.pbix` (last saved 2026-10-20 13:34). **Unsaved report edits in Desktop are not included.**

**Method:** an object counts as *used* if a visual, visual filter, page/report filter, conditional format or bookmark references it,
or if a used measure, calculated table or calculated column depends on it (followed through every chain via `INFO.CALCDEPENDENCY`).

**Result:** 210 of 468 measures and 13 of 48 user tables are unused. The 12 hidden auto-date tables are also removable.

## Tables (13)

| Table | Why it is unused |
|---|---|
| `fact_policy` | Policy-level fact; nothing on any page or in any measure reads it (pages use fact_policy_pricing / fact_earned_premium) |
| `dim_date` | Date dimension; related to 5 facts but no visual, slicer or measure uses it (visuals use fact columns such as MONTH_DATE directly) |
| `fact_scenarios` | Old preset scenario grid (pre-optimizer) |
| `fact_kpi_forecast` | Old forecast KPI cards (flat run-rate); Forecast page now uses fact_forecast_seasonal |
| `fact_timeseries` | Old forecast chart series; replaced by fact_forecast_seasonal |
| `fact_triangle_paid` | Development triangle; no page uses it |
| `fact_triangle_incurred` | Development triangle; no page uses it |
| `fact_dev_factors_paid` | Chain-ladder factors; no page uses it (Python still uses the CSV) |
| `LeverRealization` | DAX table of horizon realization %; no measure reads it (glide measures hold their own values) |
| `LeverToggles` | DAX lever list; not used (pages use ActionLevers + the 5 Toggle tables) |
| `OptWaterfallSteps` | Waterfall axis for the old Optimizer measures |
| `PriceStep` | Calculated table; not used |
| `RecMetric` | Calculated table; not used |

**Auto-date tables (12 hidden `LocalDateTable_…` + 1 template):** no visual uses a date hierarchy, so turn off *File → Options → Current file → Data load → Auto date/time*. Power BI then deletes them itself.

**Keep even though unused:** nothing flagged. *Note:* the 5 `Toggle …`, `ActionLevers`, `PTP_Timeline` and `Target Combined Ratio` tables are used.

## Measures (210)

Whole folders that are unused (likely left over from earlier page versions): `02 Pricing`, `04 Profitability Surface`, `05 Scenarios`, `06 Earned Premium`, `07 Claims`, `08 Development`, `09 Model Performance`, `10 Policy`, `13 Alerts`, `13 Optimizer`, `31 What-If`.

### (no folder) — 3 unused

`Selected Lever Value`, `Forecast Target Value`, `Claims inflation Value`

### 01 Portfolio KPIs — 4 unused

`Policy Count`, `Total Exposure Years`, `Claim Frequency`, `Average Severity`

### 02 Pricing — 9 unused

`Avg Pure Premium Per Year`, `Avg Charged Premium`, `Total Pure Premium`, `Total Charged Premium`, `Avg Expected Frequency`, `Avg Expected Severity`, `Policies Scored`, `Pricing Adequacy Ratio`, `Pricing Position`

### 03 Safe Zone — 3 unused

`Max Safe Discount`, `Premium Above Break-Even`, `Safe Zone Status`

### 04 Profitability Surface — 3 unused

`Surface Combined Ratio`, `Surface UW Profit`, `Surface Margin`

### 05 Scenarios — 12 unused

`Scenario Premium`, `Scenario Claim Cost`, `Scenario Loss Ratio`, `Scenario Combined Ratio`, `Scenario UW Profit`, `Scenario Margin`, `Baseline Premium`, `Baseline Claim Cost`, `Baseline Exposure`, `Scenario Exposure`, `Delta Premium`, `Delta Claim Cost`

### 06 Earned Premium — 1 unused

`Monthly Earned Premium`

### 07 Claims — 6 unused

`Total Paid`, `Total Incurred`, `Total Ultimate`, `Total Case Reserve`, `Total IBNR`, `Claim Count (fact_claim)`

### 08 Development — 2 unused

`Cumulative Paid`, `Cumulative Incurred`

### 09 Model Performance — 6 unused

`WAPE`, `Forecast Bias`, `MAE`, `RMSE`, `Predicted Total`, `Actual Total`

### 10 Policy — 3 unused

`Total Gross Premium`, `Motor Policy Count`, `Cancelled Policy Count`

### 11 Scenario Simulation — 6 unused

`Sim Delta Premium`, `Sim Delta Claims`, `Sim Scenario Margin`, `Sim Delta UW Profit`, `Sim Current Loss Ratio`, `Sim Current Combined Ratio`

### 12 Glide Path — 1 unused

`Glide Gap Remaining`

### 12 HTML — 5 unused

`Sim Within Safe Zone`, `Sim Status Icon`, `Sim Status Color`, `Sim Status Bg`, `Table Confidence Legend`

### 13 Alerts — 6 unused

`Alert Count Open`, `Alert Count Action`, `Alert Count Critical`, `Alert Count Warning`, `Alert Count Info`, `Alert Header Text`

### 13 Optimizer — 26 unused

`Opt Premium`, `Opt Claims`, `Opt Expenses`, `Opt Profit`, `Opt Profit Change`, `Opt Retained Exposure`, `Opt Baseline Exposure`, `Opt Target Combined Ratio`, `Opt Current Combined Ratio`, `Opt Assumption Label`, `Frontier Profit`, `Frontier Baseline Exposure`, `Opt Current Profit`, `Opt Retention`, `Opt Combined Ratio`, `Opt Premium Change`, `Opt Contraction Segments`, `Frontier Retention`, `Opt Contraction Warning`, `Opt Remaining Gap Pts`, `Opt Why Text`, `Opt Change Low Sens`, `Opt Change Base`, `Opt Change High Sens`, `Opt Range Text`, `Opt Waterfall Value`

### 14 kpi forecast — 14 unused

`Fcst Value`, `Ref Value`, `Delta`, `Delta Label`, `Delta Colour`, `Actual`, `Forecast`, `PI Lower`, `PI Upper`, `Cell Confidence Colour`, `Forecast Quarter Index`, `Forecast Adjusted`, `Monthly Loss Ratio`, `Monthly UW Margin`

### 15 Break-even — 4 unused

`Claim Count new`, `Base Cell Highlight`, `Default Growth Rate`, `Growth vs Default`

### 16 — 14 unused

`Sim Best Claims`, `Sim Best UW Profit`, `Sim Best Total Expenses`, `Sim Best Loss Ratio`, `Sim Best Combined Ratio`, `Sim Best Margin`, `Sim Active Premium`, `Sim Active Claims`, `Sim Active Total Expenses`, `Sim Active UW Profit`, `Sim Active Loss Ratio`, `Sim Active Combined Ratio`, `Sim Active Margin`, `Sim Best Status`

### 17 Action Recommender — 6 unused

`PTP Gap SAR`, `PTP Distribution CR Pts`, `PTP After Actions CR`, `Total repricing impact1`, `Segments affected 1`, `Pure Premium Needed1`

### 19 Bonus-Malus Detail — 8 unused

`BM Claims Per Policy`, `BM Avg Premium Per Policy`, `BM Surcharge Revenue`, `BM Discount Cost`, `BM Net Premium Change`, `BM Frequency Status`, `BM Exposure This Segment`, `BM Story`

### 20 UW Controls Detail — 13 unused

`UW Portfolio Avg Severity`, `UW Claim Count By Type`, `UW Total Cost By Type`, `UW Open Claims Count`, `UW Closed Claims Count`, `UW IBNR Exposure`, `UW IBNR as % of Ultimate`, `UW Case Reserve`, `UW Open Claims %`, `UW High Severity %`, `UW Story`, `UW Total Claims Cost`, `UW Severity Reduction`

### 21 Claims Mgmt Detail — 11 unused

`CM Total Claims`, `CM Closed Claims`, `CM Open %`, `CM Settlement Rate`, `CM Total Case Reserve`, `CM Reserve %`, `CM Paid %`, `CM Avg Dev Months Open`, `CM Avg Dev Months Closed`, `CM Triangle Value`, `CM Story`

### 22 Forecast Page — 2 unused

`FC CI Lower`, `FC CI Upper`

### 30 Recommendation — 12 unused

`Rec Admin`, `Rec Commission`, `Cur Admin`, `Rec Price Card`, `Rec Badge`, `Rec Insight Text`, `Rec Basis Label`, `Rec Col Current`, `Rec Col Recommended`, `Rec Col Change`, `Rec Col Change Colour`, `Curve Total Cost`

### 31 What-If — 30 unused

`WI Price`, `WI Inflation`, `WI Expense Shift`, `WI Premium`, `WI Claims Engine`, `WI Commission`, `WI Admin Engine`, `WI Retained Exposure`, `WI Claims`, `WI Expenses`, `WI Profit`, `WI Combined Ratio`, `WI Retention`, `WI Segments`, `WI Blocked Segments`, `WR Claims`, `WR Expenses`, `WR Profit`, `WR Combined Ratio`, `WC Claims`, `WC Expenses`, `WC Profit`, `WI Col Recommended`, `WI Col Mine`, `WI Col Difference`, `WI Warnings`, `WI Verdict`, `WI Verdict Colour`, `WI Bridge Value`, `WI Basis Label`

## Other findings

- Bookmarks still reference 5 measures that no longer exist (`AR Current CR`, `AR Gap to Target`, `AR Projected CR`, `AR Total SAR Recovery`, `Management Alerts HTML`). Harmless, but those bookmarks may be stale.
- Alerts appear on the Forecast page (`fact_alerts` TITLE, ISSUE, IMPACT), but the **ACTION column is not shown anywhere**. CEO feedback #4 was "alerts not actionable", so consider adding it. The `13 Alerts` count measures are unused.
