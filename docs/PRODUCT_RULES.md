# Product rule: no hard-coded assumptions

> **Status: agreed 2026-10-22, not implemented yet.** Built in `docs/TASK_PLAN.md` Task 4 (config and
> hardening) and Task 5 (real data). **Path to Profitability is out of scope for now**: its lever
> items are listed in §6 as future work and are not touched in Tasks 4–5.

## 1. The principle

Aman Prediction is a **product**. Many insurance companies will load their own data into it and set
their own business rules.

> **The code is the same for every company. Only the data and the company's settings change.**

So the code may not contain any number, list, name or date that belongs to one company or one
dataset. A value that fits one insurer will break the next one.

## 2. Every value must be one of three types

| Type | What it is | Where it lives | Examples |
|---|---|---|---|
| **1. Derived from data** | Calculated from the company's own data on every run | Python, recalculated each run | Mature years, class list, segment columns, claim-cost CV, credibility K, the target CR suggestion |
| **2. Company setting** | A business choice the insurer makes | Company settings (`config.toml` today, a settings screen later) | Target CR, expense loadings, retention floors, alert thresholds |
| **3. Method constant** | Maths or statistics, the same for every company | Code, with a comment saying why | z = 1.645 for 90% confidence, 365.25 days per year, grid step size |

**Test for any number in the code:** "Would a different insurer need a different value?"
- **Yes, and it can be calculated from their data** → type 1.
- **Yes, and it's a business choice** → type 2.
- **No** → type 3. Document it.

A value that fits none of these is a defect.

**Exempt:** `pipeline/01_build_synthetic_data.py`. It *generates* demo data, so its constants
describe the fake company, not the product. It must never run on a customer's data.

## 3. How a company setting (type 2) must behave

The pattern is already designed in `docs/TARGET_CR.md` and `docs/CREDIBILITY.md`. Every setting
follows it:

1. **One place.** It's defined once in the company settings, and every script and every Power BI
   measure reads it from there. No copies.
2. **A default from the data** wherever possible, so a new customer doesn't face empty settings.
   Example: the admin expense default is the company's actual admin expense ratio.
3. **A check against history.** The value is labelled (e.g. Realistic / Stretch / Never achieved),
   so a manager who doesn't know the history still sees what the data says.
4. **Hard limits** that reject impossible values and typos (e.g. `93` instead of `0.93`).
5. **Recorded.** Every run logs the settings it used (`data/run_log.csv`), so every number on the
   dashboard can be traced back to its settings.
6. **Python calculates, Power BI compares** (`docs/TARGET_CR.md` §6). Python outputs don't depend on
   settings that the dashboard lets people change live.

**Later, for the product:** a settings screen with approval and a change history (who changed what,
when, old and new value). Report Server can't write data back, so this will be a small separate form
or a settings table in the customer's database.

## 4. Inventory: what to change (Tasks 4–5)

Line numbers are from the 2026-10-22 scan.

### 4.1 Must be derived from data (type 1)

| Where | Today | Change to | Why it breaks on another company |
|---|---|---|---|
| `05_segment_performance_by_year.py:34` | `LATEST_MATURE_YEAR = 2023` | Detect from claim development (share of claims reported per year), with the valuation date | Every company and every extract date has different mature years |
| `07_forecast_seasonal.py:71–75` | `DATA_START = "2020-01-01"`, `MATURE_DATA_END = "2023-09-30"`, transition dates | Detect from the data's date range and the maturity rule | Fixed to the synthetic data's dates |
| `07_forecast_seasonal.py:90` | `MIN_FORECAST_LR = 0.90` (a floor on the forecast loss ratio) | **Remove**, or replace with a data check that flags rather than forces | A company with a 60% loss ratio would be forced to 90% |
| `03_pure_premium.py:50`, `05…:38`, `09_profit_optimizer.py:69` | Segment = `ZONE × CLASS_DESC × POLICY_TYPE_DESC` | Segment columns from the company's data mapping (`pipeline/contract.py`) | Another insurer may segment by region, vehicle type or channel |
| `02_glm_refit.py:55`, `04_backtest.py:52` | Fixed GLM feature list (5 columns) | Rating factors from the data mapping, keeping only those present and with enough data | Each company has different rating factors |
| `config.toml [data] motor_classes` | Three class names | Classes found in the data; config only as an optional filter | Class names differ per company |
| `09_profit_optimizer.py` (latest year) vs `06_lever_allocation.py` (all years) | Different baselines | One baseline rule (Task 1, decision #3): projected no-action CR for the plan year | Inconsistent starting points |

### 4.2 Company settings with data-driven defaults (type 2)

| Where | Today | Becomes | Status |
|---|---|---|---|
| `09…:65` | `target_combined_ratio = 0.95` | `[business.target_cr]` | Designed: `docs/TARGET_CR.md` |
| `03…:53–54`, `04…:74`, `05…:37`, `09…:64` | 300/50 policies, 30/10 claims, 30 policies | `[credibility]` | Designed: `docs/CREDIBILITY.md` |
| `05…:35–36`, `09…:50` (`config.toml [business]` is read only by `01_prepare_real.py`) | `ADMIN_RATE = 0.10`, `RISK_MARGIN = 0.05`, repeated | `[business] admin_expense_rate`, `risk_margin_rate`, read by every step | Default: the company's actual admin ratio |
| `05…:96` | Safe-zone bands at 90 / 100 / 110% CR | Bands relative to the target CR (e.g. target, 100%, 100% + margin) | To design |
| `03…:231–235` | Pricing-position bands 0.80 / 0.95 / 1.15 / 1.40 | `[pricing] adequacy_bands`, shared with the alerts | To design |
| `09…:45–49` | Retention: baseline renewal 0.90, sensitivity k = 3 / 5 / 8, γ = 0.5 | `[optimizer.retention]`. Default from the company's renewal history; labelled "assumption" until that history exists | To design (Task 11 elasticity model) |
| `09…:55–67` | Target margin, tech tolerance, retention floors 0.80 / 0.85, price range −20% / +50%, floor-sensitivity list | `[optimizer.constraints]`. Add regulatory price limits | Move as-is, then add checks |
| `09…:52–53` | External frequency trend and severity inflation = 0 | `[forecast.trend]`. Default from the forecast's fitted trend | To design |
| `11_build_alerts.py:37–53` | Loss-ratio target 0.70, critical 0.80, pricing 0.95 / 0.80, model bias 0.15, `max_alerts = 12`, … | `[alerts]`. Loss-ratio target defaults from the target CR minus the expense ratio | Move; fix the 2 dead rules |
| `07…:82–85`, `08…:52` | Forecast quality gates: WAPE 0.35, bias 0.85–1.15, coverage 0.80, band floor 0.10 | `[forecast.quality]`. Product defaults a company may tune | Move as-is |
| `10_underwriting_pnl.py:26–29` | Materiality 5% / 1% / 1 pt | `[reporting.materiality]` | Move as-is |
| `01_prepare_real.py:93` | Exposure capped at 2 years | `[prepare] max_exposure_years` | Move as-is |
| Power BI | `Target Combined Ratio` and `Full Cred Standard` what-if tables, heatmap ranges | Read from the settings tables written by Python (`fact_target_cr`, `fact_credibility_params`, …) | With the related design |

### 4.3 Method constants (type 3): keep in code, document why

| Where | Value | Note |
|---|---|---|
| `02…:64`, `04…:60` | `RARE_THRESHOLD = 30` (group rare GLM levels) | A statistical minimum, not a business rule |
| `04…:61–63` | Minimum days, policies and claims to fit a backtest cut-off | Statistical minimums |
| `04…:66`, `07…:63` | Forecast horizons 3 / 6 / 12 months | Product design; could become a setting later |
| `07…:65–66` | 90% interval width, random seed | Method |
| `09…:44` | Price grid step 1% | Search resolution |
| `01_prepare_real.py` | 365.25 days per year, 30.44 days per month | Calendar facts |

## 5. Definition of done (Task 4)

- No pipeline script (except the synthetic builder) contains a business number, a year, a date, a
  class name or a segment column list.
- Every type 2 value is read from settings, has a default (from data where possible), hard limits,
  and appears in the run log.
- Every type 3 value has a comment saying why it's a method constant.
- **Proof test:** run the pipeline on a second, differently shaped dataset (e.g. the synthetic data
  with renamed classes and shifted years). It must run with no code change.

## 6. Out of scope for now: Path to Profitability (future)

Path to Profitability is a **future step** (`docs/TASK_PLAN.md`, Future). Its hard-coded values stay
as they are until then and are **not** part of Tasks 4–5:

| Where | Today |
|---|---|
| `06_lever_allocation.py:24` | `TARGET_CR = 0.85` |
| `06…:132` | Repricing uplift capped at +50% |
| `06…:142` | Bonus-malus = 15% × frequency, capped at 5% |
| `06…:150` | UW controls = 15% severity reduction × 50% realization |
| `06…:155` | Claims management = 5% of claims |
| `06…:161` | Distribution & mix = 10% of the gap |
| Power BI `LeverRealization` | Realization % per lever and horizon |

When Path to Profitability is picked up, these become data-driven formulas and company settings,
following the redesign notes in `docs/TASK_PLAN.md` (Future).
