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
| A2 | Vehicle-level extract from `CARCDE.VCAR_MAX_VEHICLE_ENDT` (hashed chassis, dates, premium, rating fields) as `sql/vehicles.sql` | The view takes about 15 minutes per query. One extract makes renewal, price and rating work local and fast | Small | A1 if newer data is elsewhere |
| A3 | Rerun and check step 08; one source for every card | Stale KPI file (above). **Done 2026-11-10:** no visual read it; step 08 now writes only `dim_metric` and removes the old files. A0 done the same day | Small | — |

### B. Premium forecast

| # | Item | Why | Size |
|---|---|---|---|
| B1 | **Driver model**: next year's premium = **renewals** (the expiring book × the measured renewal rate × (1 + price change)) + **new business** by channel (direct, brokers, aggregator offices 93/94/96/110) | Premium jumps come from renewals and channels, which time series can't see. The chassis link now measures renewal (about 10–25% a year) | Medium |
| B2 | New business forecast per channel: recent run rate with seasonality (TimesFM is fine for this short-term piece) | TimesFM is good at 3–6 months | Small |
| B3 | Slider = the business's plan inputs (new-business growth, average price change) | The lever moves things the business decides; the model supplies the rest | Small |
| B4 | Keep TimesFM total premium as a **challenger** in the backtest | It must lose to B1 on 12M before it's dropped | Small |

### C. Claims and loss ratio forecast

| # | Item | Why | Size |
|---|---|---|---|
| C1 | **Claim development** with Bornhuetter-Ferguson (`chainladder` package, section 3) on the existing triangles | Recent claims are understated. The development test moved the no-change combined ratio from 106.7% to 119.8%. The newest quarter's development check is 0.92 (REVIEW). **Step 07 done 2026-11-10:** BF for cost (Cape Cod ELR 88.9%, written in-house, no new package), chain-ladder kept for counts; newest-quarter check 0.921 → 0.945, claims WAPE better at 3/6/12M. **Still to do:** step 01 sets `ULTIMATE_CLAIM` = incurred with no IBNR, which every page's actual loss ratio reads | Medium |
| C2 | Claims = frequency × severity × exposure from B1's book, rather than a cost rate on premium | When price changes, the cost-on-premium rate breaks. Claims follow cars, not riyals | Medium |
| C3 | Trend factor: more origins (A1), and split into frequency and severity inflation | 9 origins; the raw forecast ran 13.5 points high and got the direction right 5 times out of 9 | Small |
| C4 | Large-loss cap and reload in the monthly series | Single large months drive the 57%–121% monthly loss ratio swings | Small |
| C5 | **P10–P90 band on the Loss Ratio tab** | The flat line looks certain; the 12-month range is about 74%–129% | Small |

### D. Pricing (frequency and severity GLMs)

| # | Item | Why | Size |
|---|---|---|---|
| D1 | Add vehicle factors from the vehicle table: vehicle age (`VVCL_YEAR_OF_MAKE`, 99.9% filled), class of use (99%), driver age (`VVCL_CUST_BIRTH_DT`, 85%), vehicle value (`VVCL_SI`, 73%) | Today only generic fields (cover type, class, source, zone, sum-insured band) | Medium |
| D2 | **Previous claims of the car** through the chassis link | Usually the strongest single motor predictor | Medium |
| D3 | Protected factors (gender, nationality) stay off unless `[pricing]` switches them on | Unchanged rule | — |

### E. Retention and the optimizer

| # | Item | Why | Size | Manager |
|---|---|---|---|---|
| E1 | Baseline renewal from data, per segment (about 10–23% Third Party, 13–41% Comprehensive) instead of 90% | Measured with the chassis link; the vehicle view is complete | Small | Yes |
| E2 | **Re-set k with the baseline**: hold the price reaction (elasticity) fixed, not k. At 16% renewal, k = 5 means 6× the price reaction it has at 90% | The data can't measure k: renewers' prices move with the whole market | Small | Yes |
| E3 | Request **quote logs** from the aggregator channels (offices 93/94/96) | The only data that can measure price sensitivity: the price quoted to customers who left | Request | — |
| E4 | Retention floors become "at most X points below no-change"; the no-change scenario becomes renewals + new business (B1) | Fixed 80% / 85% floors can't be met when renewal is about 16% | Medium | Yes |

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
| G3 | Reconciliation (step 13) also checks card = chart for the Forecast page | Would have caught this automatically |
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
