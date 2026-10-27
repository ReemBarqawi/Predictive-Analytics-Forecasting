# Management alerts: method design

> **Status: approved and built 2026-10-26** (decisions §9, what was built §10). Replaces the policy-year alerts in
> `pipeline/11_build_alerts.py`. Thresholds come from the target CR (`docs/TARGET_CR.md`) and
> blending from credibility (`docs/CREDIBILITY.md`).

## 1. The problem

The alerts on the Forecast page look at one policy year (2025) and compare it with fixed thresholds.
A check on the real company book (extract of 2026-10-25) found four problems.

**1. The latest years show model predictions, not real results.**
`05_segment_performance_by_year.py` treats every policy year after a hard-coded
`LATEST_MATURE_YEAR = 2023` as immature and puts the GLM's *expected* claims in place of real claims.
Policy year 2025 has **31 real claims (SAR 124k)** against **SAR 15.5M** of model-expected claims.
An alert such as "loss ratio 633%" is the model's estimate divided by premium. Nothing like it
has happened.

**2. The model is reliable for the portfolio, not for single segments.**

| Policy year | Actual incurred (SAR) | Model expected (SAR) | Model ÷ actual |
|---|---|---|---|
| 2022 | 563M | 539M | 0.96 |
| 2023 | 408M | 408M | 1.00 |
| 2024 | 160M | 299M | 1.87 (claims still coming in) |
| 2025 | 0.1M | 15.5M | 125 (almost no claims yet) |

Segment by segment in 2023, model ÷ actual ranges from **0.27 to 5.1** (10th–90th percentile).

**3. Segments that improved are still flagged, and segments that got worse are not.**
Actual loss ratio on accident date, last 12 complete months (Jan–Dec 2024) against the 12 months before:

| Segment | Today's alert | Actual 2024 | Actual 2023 | |
|---|---|---|---|---|
| Central · Rent a Car · Comprehensive | LR 630% | 81% | 178% | much better |
| Other · Commercial · Comprehensive | LR 308% | 31% | 66% | better |
| Other · Private · Comprehensive | LR 142% | 74% | 92% | better |
| Central · Commercial · Third Party | *not flagged* | **121%** | 111% | worse |
| Eastern · Commercial · Third Party | *not flagged* | **109%** | 89% | worse |
| Western · Rent a Car · Comprehensive | *not flagged* | 78% | 61% | worse |

**4. The pipeline does not know where the data ends.** The last accident date is **2025-01-18**
and the last policy start is 2025-03-06. The SQL has no date filter, so the warehouse views
appear to stop there. The pipeline takes its valuation date from the extract file's date
(2026-10-25), so it treats 2025 as a complete year with almost no claims.

What the data supports: claims are reported and settled quickly. Each month of 2024 is 93–99%
paid by the cut-off. Only the last, partial month is badly under-reported (Jan 2025: 1,409 claims,
against about 6,000 in a normal month).

## 2. The solution in one paragraph

Alerts are **triggered by actual results** over a **rolling 12 months by accident date**, ending at
the **last complete month of claims data**. Each segment's actual loss ratio is **blended toward its
class by credibility**, so a few claims cannot raise a critical alert on their own. The model's
expected cost is shown **next to** the actual figure. **Severity comes from whether actual and model
agree**, adjusted by the **trend** against the prior 12 months. As in `docs/TARGET_CR.md`, **Python
writes only target-free facts**, and **Power BI decides every alert in DAX**, so a change to the
target updates the alerts immediately.

## 3. The measurement window (data-driven)

1. **Cut-off** = the latest accident date in `fact_claim`. A month counts as complete only if the
   data reaches its last day; otherwise it is dropped. Today the cut-off is 2025-01-18, so the last
   complete month is **Dec 2024**.
2. **Latest 12 months (L12)** = the 12 complete months ending there (Jan–Dec 2024).
   **Prior 12 months (P12)** = the 12 months before (Jan–Dec 2023).
3. **Premium** = earned premium in those calendar months (`fact_earned_premium`, by `MONTH_DATE`).
   **Claims** = incurred (paid + case reserve) on claims with an accident date in the same months.
   Premium and claims always cover the same period.
4. **Late reporting.** Before using a month, check it the way the analysis above did: its claim
   count must be at least (1 − tolerance) of the average of the previous 12 months. Drop trailing
   months that fail. The tolerance is a **method constant** (proposed 30%), not a business setting.
   Once `fact_dev_factors_paid` is fixed (see the dead `"12-24"` rule in `docs/HANDOVER.md`),
   replace this with development factors.

This replaces the policy-year filter and the `valuation_date()` / `latest_complete_year()` choice
added to `11_build_alerts.py` on 2026-10-26. No year or date is typed anywhere.

## 4. Actual and model: which drives the alert

**The alert is triggered by actual results. The model is supporting evidence.**

- A management alert reports something that **happened**. Actual results can be checked against the
  books, and managers trust them.
- Only actual results show a segment that **improved**.
- The model is too noisy per segment (§1.2) to raise alerts on its own. It is the right measure for a
  **pricing** question ("do we charge less than the modelled risk?"), and it gives early warning in
  segments with too few claims to judge.

### 4.1 Severity: do actual and model agree?

"Bad" means the combined ratio (loss ratio + expense ratio) is above the target CR. For actual
results that is the credibility-blended figure (§5); for the model it is the model's expected cost
over the same L12 premium.

| Actual (L12, blended) | Model expectation | Alert | Severity |
|---|---|---|---|
| Bad | Bad | **Underpriced for its risk.** Actual and model agree: the premium is too low for the risk mix | Critical |
| Bad | OK | **Claims above expected.** The model expects the risk mix within target: check large claims or a change in mix | Warning |
| OK | Bad | **Pricing risk.** Priced below modelled cost; results do not show it yet | Watch (pricing alert only) |
| OK | OK | No alert | none |

### 4.2 Trend moves it up or down

Trend = actual loss ratio L12 − P12, in points. A point change is "material" when it is larger
than the noise expected from the segment's claim count (2 standard errors from the segment's
own claim-size variation). This is a method constant, not a business number.

- **Improving materially:** one level down (Critical → Warning, Warning → Watch). The card says
  "improving, monitor".
- **Worsening materially:** one level up (Watch → Warning, Warning → Critical).
- **Otherwise:** unchanged.

## 5. Small segments: credibility

12 of the 29 segments had less than SAR 1M of premium in 2024. A segment's actual loss ratio is blended:

`blended LR = Z × segment LR + (1 − Z) × class LR` (class = same CLASS_DESC and POLICY_TYPE_DESC, all zones)

with Z from `docs/CREDIBILITY.md` (Bühlmann–Straub, K estimated from the company's data). Severity
uses the blended figure. The card shows the segment's own figure and the claim count, so nothing is
hidden.

## 6. What each part does

**Python (`11_build_alerts.py`, rebuilt):** writes **`fact_alert_basis.csv`**, one row per segment,
with no target and no severity:

| Column | Meaning |
|---|---|
| ZONE, CLASS_DESC, POLICY_TYPE_DESC | Segment |
| WINDOW_START, WINDOW_END, DATA_CUTOFF | L12 months and the claims cut-off date |
| EP_L12, CLAIMS_L12, CLAIM_COUNT_L12 | Actual, latest 12 months |
| EP_P12, CLAIMS_P12, CLAIM_COUNT_P12 | Actual, prior 12 months |
| COMMISSION_L12 | For the expense ratio |
| MODEL_CLAIMS_L12 | GLM expected cost on the L12 earned exposure |
| CLASS_LR_L12, CRED_Z | For the blend |
| TREND_SE_PTS | Noise band for the trend test |
| AVG_SEVERITY_L12, CLASS_AVG_SEVERITY_L12 | For the claim-cost rule |

Portfolio-level checks (settlement speed, model bias) stay in Python. They do not depend on the target.

**Power BI (DAX, folder `12 Management Alerts`):** reads `[Target CR]`, then computes per segment the
blended actual CR, the model CR, the agreement (§4.1), the trend adjustment (§4.2), the severity, and
the headline / issue / action text. The Deneb panel shows segments whose severity is not blank,
most severe first.

## 7. What the alert card shows

The period, the basis ("actual results"), the target and the data cut-off are shown **once**, in the panel
header, not on every card. Each card says what happened, whether it is likely to fix itself, and what to do:

```
Management alerts
15 open · 1 critical · 14 warning
Last 12 months (Jan 2024 – Dec 2024) · actual results · target combined ratio 95% · data to 18 Jan 2025

▲ 01 · Underpriced for its risk — worsening
      Western · Motor Rent a Car · Comprehensive
      Combined ratio: 101%, up 18 percentage points year over year. SAR 827,651 above target.
      Outlook: unlikely to improve without intervention. The pricing model also projects a 114%
      combined ratio for this risk mix.
      Reliability: Medium · 1,413 claims
      Recommended action
      Review pricing at renewal and reassess underwriting and acceptance criteria for this segment.
```

- **Title:** "Underpriced for its risk" when the pricing model also puts the risk mix above target (the
  premium is too low); "Claims above expected" when the model expects it within target (recent claims are
  unusually high). Not "loss": a segment at 98% is above a 95% target without making a loss.
- **Outlook line:** *"unlikely to improve without intervention"* or *"may improve"*, with the model's projected
  combined ratio (model expected claims plus the same commission and admin expense).
- **Reliability** in words from the credibility bands (`docs/CREDIBILITY.md` §6): high ≥ 0.70, medium 0.30–0.70,
  low < 0.30 (the headline then adds "limited history"). The Z value itself is not shown.
- Right-click → Drill through opens the segment's pages.

## 8. Changes this needs elsewhere

- `05_segment_performance_by_year.py`: replace `LATEST_MATURE_YEAR = 2023` with a maturity test
  from the data (the share of a year's claims already paid), and keep `CLAIMS_BASIS` honest.
- `settings.valuation_date()`: take the date from the data (the latest accident date), not the
  extract file's date. The same mistake affects claim development months in `01_prepare_real.py`.
- Ask the DBA whether `V_UW_DATA` and the claims view really end in early 2025. If they do, every
  page of the report shows the book as of January 2025.

## 9. Decisions (approved 2026-10-26)

1. **Window length:** 12 months.
2. **Severity rule:** the agreement table in §4.1 and the trend adjustment in §4.2, as written.
3. **Panel content:** **Critical and Warning only.** "Watch" (pricing risk: model bad, actual fine)
   is not shown on the alert panel; it belongs on the pricing pages.

Until `docs/TARGET_CR.md` is built, the target is the existing `[Target Combined Ratio Value]` slider
(default 0.95). When `[Target CR]` exists, the alert measures switch to it in one place.

## 10. What was built (2026-10-26)

**Python:** `pipeline/11_build_alerts.py` writes `fact_alert_basis.csv` (§6) and keeps the two
portfolio checks in `fact_alerts.csv`. The old policy-year segment rules are gone.

**Power BI:** table `fact_alert_basis`, related many-to-one to `dim_zone_summary`, `dim_business_class`
and `dim_product`. Measures in `measures table` → `12 Management Alerts`: `Alert Target CR` (the one
place the target is read), the ratios, `Alert Base Level` (§4.1), `Alert Trend Direction` (§4.2),
`Alert Level`, `Alert Severity`, the card text (`Alert Headline`, `Alert Issue`, `Alert Evidence`,
`Alert Action`, `Alert Period`) and `Alert Sort`.

**Panel:** Deneb, provider **Vega**, spec `powerbi/deneb/management_alerts.vega.json`. Fields, all from
the dims or measures (no `fact_alert_basis` columns, so drill-through passes the segment):
`ZONE`, `CLASS_DESC`, `POLICY_TYPE_DESC`, `Alert Severity`, `Alert Headline`, `Alert Issue`,
`Alert Evidence`, `Alert Action`, `Alert Period`, `Alert Sort`.

### Where the build differs from §2–§6, and why

1. **The model is re-based per class before use.** On 2022–23 policy years the GLM gives 3.6–5.1×
   actual claims for Motor Commercial Comprehensive and 0.29–0.57× for Motor Commercial Third Party.
   Used as is, "model bad" would be decided by the class, not the segment. `MODEL_CLAIMS_L12` is
   scaled per class (class + policy type) by actual ÷ model over all months before the window
   (`CLASS_MODEL_FACTOR`), so the model contributes only how a segment differs from its class.
   **The GLM's class calibration needs fixing in steps 02–03; this is a workaround.**
2. **Credibility is estimated on the segment's loss ratio relative to its class in the same
   12-month block.** On raw loss ratios, swings that hit a whole class (inflation, market cycle)
   counted as segment noise and every Z came out 0. Result on the real book: K = SAR 15.6M of earned
   premium; Z runs from 0.00 (tiny segments) to 0.96 (Other · Private · Third Party).
3. **Blend toward the class, not the model.** `docs/CREDIBILITY.md` §7 blends toward the
   model-expected loss ratio. Given item 1, the class's actual loss ratio is the safer complement here.
4. **Low credibility (Z < 0.30, `docs/CREDIBILITY.md` §6):** cannot be Critical, the card says
   "review (low credibility)", **and the segment's own actual combined ratio must also be above
   target.** Without that last rule a segment with SAR 1.8k of premium and a 25% combined ratio was
   flagged "Confirmed loss" because its class is losing money.
5. **Trend is "material" beyond 2 standard errors** of the change, from the segment's own claim
   amounts (compound Poisson), as stated in §4.2.

### Result on the real book (target 95%, window Jan–Dec 2024, data to 18 Jan 2025)

15 alerts: **1 Critical** (Western · Rent a Car · Comprehensive: combined ratio 101%, +18 pts,
model agrees) and **14 Warning**. The largest by SAR above target is Central · Commercial · Third Party
(combined ratio 139%, SAR 2.2M above target). Only 3 of the 15 have credibility ≥ 0.30, so most
alerts carry "review (low credibility)". That is a true statement about the book: the loss-making
classes (Motor Commercial, Rent a Car) are made of small segments.
