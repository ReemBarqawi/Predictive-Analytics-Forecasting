# Model improvement plan (2026-11-10)

Starting point: the Forecast page shows two premium figures for 2025, SAR 382.9M on the *Expected premium* card and
SAR 390.0M on the chart. This document rates that issue, traces it to its cause in the model, and sets out a plan for
improving every part of the model. Figures come from the real (Oracle) run of 2026-11-05 unless stated.

## 1. The Forecast page issue

### What is confirmed

| Point | Evidence |
|---|---|
| The model already forecasts growth | 2025 written premium 382.9M vs 358.9M in 2024 = **+6.7%** (`fact_forecast_seasonal`, 12M) |
| The slider adds growth on top, chart only | Chart 390.0M = 382.9M plus the 4% slider spread over the months. The cards stay on 382.9M |
| The subtitle is wrong | "assuming 4% annual growth": the line shows about +8.7% on 2024 |
| The target slider does nothing on Premium | It only draws the Loss Ratio target line |
| **Also found:** the KPI cards' file is stale | `fact_kpi_forecast.csv` is from 2026-11-03, older than the forecast (11-05). Its 4-quarter premium is 505M and its loss ratio 25.7%, which agree with nothing else on the page. Step 08 must be rerun and checked before any card reads it |

(The DAX could not be read: Power BI Desktop was closed. The chart figure is the one reported from the page.)

### Rating

| | Rating | Why |
|---|---|---|
| Size of the error | **Low**: 7.1M, 1.9% at the default 4% | Small next to the forecast's own range: P10–P90 of 12-month totals is about 282M–554M |
| Effect on trust | **High** | Two numbers for the same thing on one page, and a subtitle that says 4% when the line shows 8.7%. A manager who spots one inconsistency stops trusting the page |
| Effect on decisions | **Medium** | Nobody prices from this chart, but premium plans and targets are read from it. The slider gives "what if" a precision the model doesn't have |
| **Overall** | **Medium–high: fix before the demo** | Cheap to fix and visible to everyone |

### The real cause is the premium model

The slider was added because the premium forecast can't be trusted 12 months ahead, so the business wanted a lever.
Two sources of growth then stack on each other. The data shows why the model is weak:

| Policy year | 2020 | 2021 | 2022 | 2023 | 2024 |
|---|---|---|---|---|---|
| Written premium (M) | 315.6 | 351.6 | 617.8 | 607.1 | 358.9 |
| Change | | +11% | **+76%** | −2% | **−41%** |

- These jumps are **business decisions**, not patterns: the move from Direct to Brokers and aggregators in 2022–23 (Direct
  Third Party 766k policies in 2022 → 19k in 2023; Brokers 17k → 582k), then the 2024 contraction. A time-series model (TimesFM)
  only sees the monthly totals, so it can't know about them.
- **Accuracy by horizon** (rolling backtest, earned premium): 3 months 2.4% error vs 5.8% for "repeat last year"; 6 months
  6.3% vs 8.7%; **12 months 13.4% vs 12.8%, no better than repeating last year**, and 8% low on average.
- **Past 12-month totals missed by 0.83× to 2.01×** of the forecast (9 origins, written basis). The typical actual came in 17% above.
- **The data ends on 2025-01-20 by design.** The warehouse is a backup of the client's data, not a live copy, so the
  report treats **2024 as the current year and 2025 as the forecast year**. The pipeline already works this out from the
  data (`current_year()`: the last calendar year the claims cover fully). The as-of date must follow the same rule (A0).

### Fix

**Now (page, about a day):** option A.
1. The slider becomes **"Adjustment to the model's forecast"**, default **0%**. One measure, `Forecast Premium Adjusted =
   [Forecast Written Premium] × (1 + slider)`, feeds the chart **and** every card, so the page can't disagree with itself.
2. Subtitle: "Model forecast +6.7% on 2024" plus "your adjustment +X%" only when X ≠ 0.
3. Premium tab: add the line **"Premium needed for the target"** = forecast claims ÷ (target combined ratio − expense ratio).
   That turns the target slider into a real answer ("how much premium the claims need"), not a decoration.
4. Rerun step 08, and either point the cards at step 07's figures or remove `fact_kpi_forecast` from the page.

Option B (the slider replaces the model's growth) is easier to read, but it throws away the claims-rate and seasonal work.
It's the right idea only for premium, and only once premium has a proper driver model (section 2, B1). Until then, A.

**Next (model):** replace the premium time series with a **driver model** (B1 below). The slider then becomes the plan's
new-business and price assumptions, which are things the business actually decides.

## 2. Improvement plan, all parts of the model

Each item: what changes, why, the evidence, size, and what it depends on. **Every change has to beat the current model on
the rolling backtest** (12 origins at 3M, 9 at 12M) before it replaces it, and the backtest runs on Oracle data only.

### A. Data foundations (blocks the most)

| # | Item | Why | Size | Depends on |
|---|---|---|---|---|
| A0 | **As-of date = the data's cut-off**, not the extract file's date: `valuation_date()` falls back to the last claim date in the extract (18 Jan 2025) | Today the fallback is the file date (25 Oct 2026). Open claims then count about 21 extra months of development, and the date table runs to 2026. Each client backup gets its own current year and forecast year with no years typed in | Small | — |
| A1 | Newer data only if the client sends a newer backup. The years then move forward on their own (A0) | The backup ends 2025-01-20 by design: 2024 = current, 2025 = forecast | — | Client |
| A2 | Vehicle-level extract from `CARCDE.VCAR_MAX_VEHICLE_ENDT` (hashed chassis, dates, premium, rating fields) as `sql/vehicles.sql` ✅ *2026-11-11* | The view takes about 15 minutes per query. One extract makes renewal, price and rating work local and fast. Built: chassis hashed in Oracle, driver age instead of birth date, last `[oracle] vehicles_years` = 4 policy years (all history ran for over an hour; 4 years take ~7 minutes, 1.9M rows). `fact_vehicle.parquet` (step 01), 100% matched to policies | Small | A1 if newer data is elsewhere |
| A4 | **Extract all history; each model chooses its training window** ✅ *2026-11-11* | `[oracle] first_policy_year = 0` (was a typed 2019). Training on all of 2008+ made every model worse on recent years (old book before the channel shift, a 2010 gap). Now `training_window.py`: each model tests 3 / 5 / 7 years / all on its backtest and keeps the longest window within one standard error of the best; `[training] window_years` overrides. GLMs are scored on relativities (level-corrected), since pricing takes the level from reserving × trend: chose **3 years** (10.2% ±1.0% vs 40–63%), and rank risk better than the 2019+ GLMs (recent segment cost 3m 11.4% vs 12.1%). Forecast scored on 3/6/12-month totals and the 12-month loss ratio: all windows within noise, so **all history** (2025 loss ratio 101.9%). Power BI still shows the last 5 years. Old records with impossible dates are dropped in step 01 (94 rows, SAR 3.3M) | Medium | — |
| A3 | Rerun and check step 08; one source for every card | Stale KPI file (above). **Done 2026-11-10:** no visual read it; step 08 now writes only `dim_metric` and removes the old files. A0 done the same day | Small | — |

### B. Premium forecast

| # | Item | Why | Size |
|---|---|---|---|
| B1 | **Driver model**: next year's premium = **renewals** (the expiring book × the measured renewal rate × (1 + price change)) + **new business** by channel (direct, brokers, aggregator offices 93/94/96/110) ✅ *2026-11-11, renewals + total new business (by channel still open, B2)* | Premium jumps come from renewals and channels, which time series can't see. The chassis link now measures renewal (about 10–25% a year). Built: `premium_drivers.py`; renewal premium follows the premium expiring that month (correlation 0.86, renewals 11–36% of a month); new business repeats only loosely (0.42). Step 07 keeps it only if it beats the total forecast by more than its standard error (G2): written premium error 24.9% ±2.5% vs 27.5% ±2.0% over 12 origins → **in use**. Next 12 months: renewals 56.4M + new business 292.8M = 349.2M (−2.7% on 2024; the total forecast had +8.2%). Months now rise where cohorts expire (Aug–Oct, Dec; max/min 1.26× vs 1.15×); cards still near ×2 because new business (84%) stays smooth. Needs `[oracle] vehicles_years = 7` | Medium |
| B2 | New business forecast per channel: recent run rate with seasonality (TimesFM is fine for this short-term piece) ✅ *built 2026-11-12, re-tested each run; not used on this data* | TimesFM is good at 3–6 months. Channels = SOURCE_DESC with ≥5% of new business in the 12 months before the origin (smaller ones "Other"). The gate goes total → renewals + new business → by channel, a step only when it wins by more than its noise: 27.5% ±2.0% → 24.9% ±2.5% → by channel **26.7% ±3.9%, not taken**. Reason: the channels swapped in 2023 (Direct 493M → 56M, Brokers 27M → 468M), so each channel's history breaks while the total stays smooth. Will switch on for a company with stable channels. Aggregator offices (93/94/96) would need an office-level channel | Small |
| B3 | Slider = the business's plan inputs (new-business growth, average price change) | The lever moves things the business decides; the model supplies the rest | Small |
| B4 | Keep TimesFM total premium as a **challenger** in the backtest | It must lose to B1 on 12M before it's dropped | Small |
| B5 | ~~Spread the year by past seasonal shape~~ **Tested 2026-11-10, rejected**; **retested 2026-11-11 as a damped shape, now chosen each run** | Step 07 scores a 0/25/50/75/100% mix of the last 3 years' monthly shares with the model's own shape (cards' 3M/6M totals + monthly error, page basis) and keeps the smallest weight within one standard error of the best (`fact_forecast_shape.csv`). On Oracle: 0% 31.8% ±3.2%, 50% 30.6% ±2.3% → **flat kept**; the gain (1.2 pts) is inside the noise. January starts gain most (3M premium 25.8% → 17.3%) but are only 3 origins. It switches on by itself when a company's data shows a stable season. The real fix for the month shape is B1 (expiry-driven renewals). Earlier note: | The 3M / 6M / 12M cards are close to ×2 because TimesFM's monthly path is flat, while past years' second halves were 1.3–1.6× the first. On the page's written-month basis (12 origins), shaping the model's 12-month total by the last 3 years' monthly shares made premium worse (3M error 27.5% vs 23.8%, bias −24% vs −11%) and claims only slightly better (3M 24.0% vs 29.7%). The flat path matches on average (first 6 months 51.9% of the year vs 52.3% actual). From January origins the errors (−50% to +44%) come from the level, not the shape: B1 is the fix | — |

### C. Claims and loss ratio forecast

| # | Item | Why | Size |
|---|---|---|---|
| C1 | **Claim development** with Bornhuetter-Ferguson (`chainladder` package, section 3) on the existing triangles | Recent claims are understated. The development test moved the no-change combined ratio from 106.7% to 119.8%. The newest quarter's development check is 0.92 (REVIEW). **Step 07 done 2026-11-10:** BF for cost (Cape Cod ELR 88.9%, written in-house, no new package), chain-ladder kept for counts; newest-quarter check 0.921 → 0.945, claims WAPE better at 3/6/12M. **Reserving done 2026-11-10:** new step 14 (`fact_claims_ultimate`) is the one source of ultimate claims for every page and year (BF, written-year basis); steps 05, 07, 09 read it and the gate checks it. Provisional until the actuary reviews it (`docs/TASK_PLAN.md` decision 6) | Medium |
| C2 | Claims = frequency × severity × exposure from B1's book, rather than a cost rate on premium | When price changes, the cost-on-premium rate breaks. Claims follow cars, not riyals | Medium |
| C3 | Trend factor: more origins (A1), and split into frequency and severity inflation | 9 origins; the raw forecast ran 13.5 points high and got the direction right 5 times out of 9 | Small |
| C4 | Large-loss cap and reload in the monthly series ✅ *built 2026-11-12, re-tested each run; not used on this data* | Single large months drive the 57%–121% monthly loss ratio swings. Measured: the 99.5th percentile of claim size (5 years) is SAR 63,000; above it sits only 5.1% of claims cost (0.4%–10.4% of a month). Step 07 can learn the claims rate from capped claims + an even load (the history's own excess share); the gate scores it against the real claims: raw 15.9% ±2.4%, capped + load 16.5% ±3.0% → **raw kept** (`fact_forecast_large_loss.csv`). The swings come mostly from volume, not single claims | Small |
| C5 | **P10–P90 band on the Loss Ratio tab** ✅ *2026-11-11* | The flat line looks certain. Built like the premium and claims bands (past months' actual / forecast loss ratio, coverage 74% out of sample): about ×0.58 to ×1.53 of each month, i.e. 57%–155% around ~102%. Columns LOSS_RATIO_P10 / P90 at the end of fact_forecast_seasonal; `Chart CI Upper / Lower` draw it | Small |

### D. Pricing (frequency and severity GLMs)

| # | Item | Why | Size |
|---|---|---|---|
| D1 | Add vehicle factors from the vehicle table: vehicle age (`VVCL_YEAR_OF_MAKE`, 99.9% filled), class of use (99%), driver age (`VVCL_CUST_BIRTH_DT`, 85%), vehicle value (`VVCL_SI`, 73%) ✅ *2026-11-12: vehicle age and driver age bands* | Today only generic fields (cover type, class, source, zone, sum-insured band). Built in step 01 (fleets: mean vehicle age, median driver age); 96.7% of 2022–24 policies covered. Class of use is built but not rated on: one code is 99%, and its thin levels made the fit unstable. Vehicle value: sum-insured band already rates it | Medium |
| D2 | **Previous claims of the car** through the chassis link ✅ *2026-11-12* | Usually the strongest single motor predictor. `PRIOR_CLAIMS` = claims on the car's previous policy with the company ("new to us" when none known): 1+ → frequency ×1.98, driver <25 ×1.27, 65+ ×0.83. Step 04 tests the GLMs with and without the vehicle factors (relativity WAPE, chosen window): 10.2% ±1.0% → **9.2% ±0.8%**, kept (`fact_rating_factors.csv`; steps 02/03 follow it). Fixes on the way: duplicate dummy columns are dropped (identical "Unknown" columns sent coefficients to 1e11), and the refit warns on any relativity beyond ×1,000. Segment prices barely move (levels come from reserving × trend); per-policy pure premiums do | Medium |
| D3 | Protected factors (gender, nationality) stay off unless `[pricing]` switches them on | Unchanged rule | — |

### E. Retention and the optimizer

| # | Item | Why | Size | Manager |
|---|---|---|---|---|
| E1 | Baseline renewal from data, per segment (about 10–23% Third Party, 13–41% Comprehensive) instead of 90% ✅ *2026-11-11, provisional* | Measured with the chassis link; the vehicle view is complete. Step 17 (`fact_renewal_rates`): same car insured again −30/+60 days around expiry, latest 12 observable months: **13.6%** of 624k cars (segments 10.7%–51.8%; Central Private TP 12.0%, Commercial 26–39%, Rent a Car 47–52%), credibility-blended. The optimizer uses it per segment; `baseline_renewal` is only the fallback | Small | Yes |
| E2 | **Re-set k with the baseline**: hold the price reaction (elasticity) fixed, not k. At 16% renewal, k = 5 means 6× the price reaction it has at 90% ✅ *2026-11-11, provisional* | The data can't measure k: renewers' prices move with the whole market. Built: `price_reaction_reference_renewal = 0.90`; k × (1 − renewal) held fixed (base k 5 → 0.57 at 12%) | Small | Yes |
| E3 | Request **quote logs** from the aggregator channels (offices 93/94/96) | The only data that can measure price sensitivity: the price quoted to customers who left | Request | — |
| E4 | Retention floors become "at most X points below no-change"; the no-change scenario becomes renewals + new business (B1) ⏳ *first half done 2026-11-11, provisional* | Fixed 80% / 85% floors can't be met when renewal is about 16%. Built: `floor_basis = "relative"`, floors = share of the no-change renewers kept, set to the old strictness (0.80/0.90 = 89%, 0.85/0.90 = 94%). Result: recommended +12.0% (exposure-weighted), result −2.8M on a 62M renewal book, 9 of 22 segments reach target. **Still open:** the no-change book is renewals only (62M of 359M); new business needs B1. Power BI What-if / Safe Zone / curve measures moved to the same relative basis; the What-if bridge still starts from today's whole book | Medium | Yes |

### F. Other lines of business

| # | Item | Why | Size |
|---|---|---|---|
| F1 | FGA (Fire, Liability, Engineering, General Accident) and Marine: credibility loss-ratio pricing, development from C1's method, large-loss cap. Flow in section 3 | 1,289 and 241 claims over 6 years: too few for GLMs | Medium |
| F2 | Checks first: Fire's 1,149M premium gross vs net; the 15% of FGA claims that don't match a policy | The wrong premium basis or a broken key would give wrong prices | Small |
| F3 | Medical: find the claims source (`MEDCDE` has policies and renewal fields) | No medical claims in `V_CLM_DATA` | DBA |

### G. Checks and governance

| # | Item | Why |
|---|---|---|
| G1 | One measure per figure (already the rule in `docs/METRIC_STANDARD.md`): chart and cards read the same measure | This page's issue |
| G2 | Backtest gate: a model change ships only if it beats the current model and "repeat last year" at 3, 6 and 12 months | 12M premium fails today |
| G3 | Reconciliation (step 13) also checks card = chart for the Forecast page ✅ *2026-11-11* | Would have caught this automatically. Now: 3M and 6M cards = the first 3 / 6 months of the 12-month line (premium, claims), and every forecast month inside its own band (premium, claims, loss ratio). 92 of 92 checks pass. The What-if slider is DAX only, so the chart's adjusted line is outside this check by design |
| G4 | Manager-review items stay parked in `docs/TASK_PLAN.md` and are reviewed together at the end of the prototype | Unchanged rule |

## 3. Tools and starting code

Free, open-source code to start from and adapt. **Check each licence before it goes into the product.** Nothing here is a
pretrained pricing model: prices come from the company's own claims.

| For | Tool | Use | Plan items |
|---|---|---|---|
| Credibility pricing (FGA, Marine) | [`insurance-credibility`](https://pypi.org/project/insurance-credibility/) (PyPI) | Bühlmann-Straub for commercial lines; unequal exposures and nested groups (class × policy type → department). Input: one row per group per year with exposure and loss ratio. **First choice** | F1 |
| | [`credibility`](https://pypi.org/project/credibility/) (PyPI) | Lighter Bühlmann-Straub (NumPy and Polars only); thin groups borrow from their parent. Fallback if the first is too heavy | F1 |
| Claim development | [chainladder-python](https://chainladder-python.readthedocs.io/main/gallery/index.html) (Casualty Actuarial Society) | Standard reserving library. Gallery notebooks: [Bornhuetter-Ferguson vs Chainladder](https://chainladder-python.readthedocs.io/main/gallery/plot_benktander.html) and choosing the BF a priori; [IBNR methods guide](https://chainladder-python.readthedocs.io/stable/user_guide/methods.html); [CAS overview of open-source actuarial tools](https://www.casact.org/sites/default/files/2022-11/CS-24_Open-Source_Actuarial_Science_with_Examples.pdf). BF suits thin lines because it leans on an expected loss ratio | C1, F1 |
| Motor GLM reference | [glum French motor tutorial](https://glum.readthedocs.io/en/latest/tutorials/glm_french_motor_tutorial/glm_french_motor.html) | Worked frequency, severity and Tweedie GLM example to check D1–D2 against | D1, D2 |
| | [Burning Cost pricing course](https://burning-cost.github.io/2026/03/25/modern-insurance-pricing-python-databricks-course-free) (GitHub `burning-cost/course`) and their [tools list](https://burning-cost.github.io/awesome) | Twelve modules covering a whole pricing pipeline. One company's material written as Databricks notebooks: examples, not a standard | D, B |
| Optional challenger | [TabPFN insurance claims cookbook](https://docs.priorlabs.ai/cookbook/insurance_claim_modeling) | Compares TabPFN with GLMs on motor data. Test only, through G2's backtest gate; its newer versions are non-commercial | D (challenger) |
| Forecasting | TimesFM (already in `pipeline/timesfm_model.py`) | Keep for 3–6-month pieces (B2) and as the premium challenger (B4) | B2, B4 |

**How the FGA and Marine pieces fit together (F1)**
1. `chainladder` (Bornhuetter-Ferguson) estimates where each accident year's claims will end up, open estimates included
   (FGA: 128M estimated vs 24M paid).
2. Divide by earned premium to get each class × policy type's loss ratio for each year.
3. `insurance-credibility` blends each class's loss ratio toward FGA's, and FGA's toward the **market loss ratio**, which is a
   company setting each company fills in from its market report (not a number written into the code).
4. The existing optimizer step (09) turns the blended loss ratio into a price change against each line's target.

## 4. Order

1. **A0 as-of date**, then the **page fix** (option A, the premium-needed line, rerun step 08).
2. **C1 claim development** and **C5 band**: they correct today's numbers and show their uncertainty.
3. **A2 vehicle extract**, then **D1–D2** (GLM factors) and **E1–E2** (retention from data, parked for the manager).
4. **B1–B3 driver premium model**. This is the real fix behind the slider, and it uses E1's renewal rates.
5. **F** other lines, once F2's checks come back.

Manager decisions collected from this plan: E1, E2, E4, the targets and expense rates for other lines, and whether the
Premium tab shows the premium-needed line.
