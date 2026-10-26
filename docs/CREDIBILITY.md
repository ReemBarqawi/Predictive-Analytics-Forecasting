# Credibility: design

> **Status: designed 2026-10-22, not implemented yet.** Build it when the pipeline is connected to
> real data (`docs/TASK_PLAN.md` Tasks 4–5). The method and the fallback preset are the manager's
> decision (Task 1, decision #2). Record them in `docs/HANDOVER.md` §9.
>
> **Product principle:** this is a product that each company loads its own data into. No credibility
> number may be hard-coded for one company. The standard is **estimated from each company's data**.

## 1. The problem

Credibility answers one question: *how much can we trust a segment's own history?* Today four
different rules answer it:

| Where | Rule | Problem |
|---|---|---|
| Steps 01, 05, 06: segment tables, safe zone, levers | Full ≥ 30 claims, Partial ≥ 10 | Too loose: 25 of 34 synthetic segments are "Full". |
| Steps 03, 04: pure premium, backtest | Full ≥ 300 policies, Partial ≥ 50 | Counts policies, but reliability comes from claims and exposure. |
| Step 09: optimizer | ≥ 30 policies, else borrow from the parent segment | A minimum size to simulate, not a credibility rule. |
| Power BI | `Full Cred Standard` what-if = 1,082 claims | Disconnected from everything else. |

Pass/fail flags also cause two problems:
- **A cliff edge:** a segment with 29 claims and one with 31 are treated completely differently.
- **All or nothing:** a "Low" segment is either hidden or shown as if it were reliable.

## 2. Why not a fixed number such as 1,082

1,082 is the classical "limited fluctuation" standard:

`n_full = (z / k)²`

- **z:** the confidence level. 90% gives z = 1.645.
- **k:** the tolerance. ±5% gives k = 0.05.
- **Result:** (1.645 / 0.05)² = 1,082 **claims**.

It is a convention, not a law. It also only covers claim **frequency**. Covering claim **size** too
multiplies it by (1 + CV²), where CV is the coefficient of variation of claim cost. On the synthetic
data CV = 2.5, so the standard becomes **7,854 claims**:

| Preset | Confidence, tolerance | Frequency only | With claim size (CV 2.5) |
|---|---|---|---|
| Strict | 90%, ±5% | 1,082 | 7,854 |
| Standard | 90%, ±10% | 271 | 1,963 |
| Lenient | 80%, ±10% | 164 | 1,192 |

A fixed number therefore suits one company and fails another. A small insurer would see almost every
segment as "Low"; a large one might see everything as "High".

## 3. The solution in one paragraph

Every segment gets **one credibility score Z between 0 and 1**, computed the same way in Python and
Power BI. Z is estimated with **Bühlmann–Straub credibility**, whose constant K is **estimated from
each company's own data**. When K can't be estimated, the **classical formula** is used with a preset
the company picks, and CV calculated from their claims. Figures are **blended** by Z between the
segment's own experience and the model's expectation, instead of hiding segments. The **bands and the
dashboard look** are the same for every company, because they are defined on Z, not on claim counts.

## 4. Default method: Bühlmann–Straub

### 4.1 Idea

`Z = n / (n + K)`

- **n:** the segment's own volume, in exposure-years.
- **K = within-segment noise ÷ between-segment differences.**
  - **Noise (EPV, expected process variance):** how much a segment's yearly results jump around.
  - **Differences (VHM, variance of hypothetical means):** how much segments truly differ from
    each other.

How this behaves:
- **Segments truly differ** (one zone really is riskier): K is small, Z is high, and each segment's
  own history is trusted.
- **Segments are similar, and the differences are mostly noise:** K is large, Z is low, and the
  figures lean on the model.

The company's data decides. No constant is chosen by anyone.

### 4.2 Estimation (Python, once per run)

Data: segment × mature policy year, last `lookback_years` years.
- **X_ij:** loss cost (ultimate claims ÷ exposure-years) of segment i in year j.
- **m_ij:** exposure-years (the weight).
- Segments with fewer than 2 years are skipped.

```
m_i  = Σ_j m_ij                         X_i  = Σ_j m_ij·X_ij / m_i          (segment mean)
m    = Σ_i m_i                          X̄    = Σ_i m_i·X_i / m              (portfolio mean)
r    = number of segments               n_i  = number of years of segment i

EPV  s² = Σ_i Σ_j m_ij·(X_ij − X_i)²  /  Σ_i (n_i − 1)
VHM  a  = [ Σ_i m_i·(X_i − X̄)²  −  (r − 1)·s² ]  /  ( m − Σ_i m_i² / m )

K    = s² / a                           Z_i = m_i / (m_i + K)
```

**When estimation fails**, switch to the classical fallback (§5) and say so in the output:
- `a ≤ 0`: no measurable difference between segments,
- fewer than `min_segments` segments, or
- fewer than 2 mature years.

**Worked example (synthetic data, 2019–2023, 32 segments):**
- **K:** 76 exposure-years.
- **Z:** median 0.65, range 0.04–0.95.
- **Bands:** 15 High, 13 Medium, 4 Low.
- **Readable form:** "a segment needs about 177 exposure-years to reach High".

The synthetic generator builds in strong segment differences, so real data will give a different K.
That is the point.

### 4.3 Refinement (when real data is connected)

Estimate on the **actual ÷ model-expected ratio** (A/E against the GLM) instead of raw loss cost.
Credibility then measures "how far to trust the segment's deviation from the model". This matches the
blend in §7. Raw loss cost (above) is the simpler first version; compare the two on real data.

## 5. Fallback method: classical (limited fluctuation)

`Z = MIN(1, SQRT(claims / n_full))`, with `n_full = (z / k)² × (1 + CV²)`

- **z and k:** from the preset the company picks: Strict, Standard or Lenient (§2 table). Default:
  **Standard**.
- **CV:** calculated automatically from the company's own claim amounts on every run. Nobody types
  it.

## 6. Bands (the same for every company)

| Band | Z | Badge | What the dashboard and pipeline do |
|---|---|---|---|
| High | ≥ 0.70 | solid dot | Normal display; can drive decisions automatically |
| Medium | 0.30–0.70 | half dot | Normal display with a badge |
| Low | < 0.30 | hollow dot, greyed | "Directional only"; any action needs manual review |

**Decision rules:**
- A **Low** segment can't raise a *critical* alert or an automatic repricing recommendation. It gets
  **"Review"** instead.
- The backtest reports segment accuracy for **Z ≥ 0.30**.

## 7. Blend, don't hide

`Credible loss ratio = Z × actual loss ratio + (1 − Z) × model-expected loss ratio`

- **The model-expected figure already exists:** step 05 writes `MODEL_EXPECTED_CLAIMS` from the GLM.
- **Pages that use the credible figure:** safe zone, levers, repricing and target comparisons.
- **The actual figure stays visible**, side by side with the credible one.
- **Grain:** single-year segment figures are mostly noise. Segment decisions use the pooled
  multi-year window (Task 1, decision #3); single years are for trends only.

## 8. Keep "too small to model" separate

The optimizer's ≥ 30 policies, with its fallback to the parent segment, stays. It is renamed to
`min_policies_to_model` and lives next to credibility in config. It answers a different question:
*can we simulate this segment at all?*

## 9. Implementation spec

### 9.1 Config (`config.toml`)

```toml
[credibility]
method          = "buhlmann"     # "buhlmann" (data-driven) or "classical"
lookback_years  = 5              # mature years used to estimate K
min_segments    = 5              # fewer → classical fallback
fallback_preset = "standard"     # [MANAGER DECISION] strict | standard | lenient
include_severity = true          # classical: multiply by (1 + CV²), CV from the data
bands           = [0.30, 0.70]   # Low < 0.30 ≤ Medium < 0.70 ≤ High
min_policies_to_model = 30       # optimizer: below this, borrow from the parent segment

[credibility.presets]
strict   = { confidence = 0.90, tolerance = 0.05 }
standard = { confidence = 0.90, tolerance = 0.10 }
lenient  = { confidence = 0.80, tolerance = 0.10 }
```

### 9.2 Python

- **New module `pipeline/credibility.py`:**
  - `estimate()` returns the method used, K (or n_full), EPV, VHM, CV, the reason for any fallback,
    and the counts per band.
  - `z(volume)` and `band(z)` apply it.
- **Step 05:** add `CREDIBILITY_Z`, `CREDIBILITY_LEVEL` and `CREDIBLE_LOSS_RATIO` to
  `fact_segment_performance_by_year`. Write `fact_credibility_params.csv` (one row).
- **Steps 01/prepare, 03, 04, 06:** replace their own thresholds with `credibility.z()` and
  `credibility.band()`. Step 03 and step 04 stop counting policies.
- **Step 09:** read `min_policies_to_model` from config; rename `credibility_level` to avoid
  confusion with the bands.
- **Step 11:** downgrade *critical* to *review* for Low segments.
- **Target CR** (`docs/TARGET_CR.md`): a "credible year" becomes Z ≥ 0.30 instead of 30 claims.
- **Run log:** record the method, K and the band counts.

### 9.3 Output `02_facts/fact_credibility_params.csv` (one row)

`METHOD` (Bühlmann / Classical), `FALLBACK_REASON`, `K_EXPOSURE_YEARS`, `N_FULL_CLAIMS`, `EPV`,
`VHM`, `CLAIM_CV`, `PRESET`, `BAND_LOW`, `BAND_HIGH`, `SEGMENTS_HIGH`, `SEGMENTS_MEDIUM`,
`SEGMENTS_LOW`, `YEARS_USED`, `RUN_TS`.

### 9.4 Power BI

Load `fact_credibility_params`. Delete the `Full Cred Standard` what-if table. Z is recomputed from
the filtered totals, so it is right at any grain: segment, zone, class, one year or all years.

```DAX
Cred K       = MAX ( fact_credibility_params[K_EXPOSURE_YEARS] )
Cred N Full  = MAX ( fact_credibility_params[N_FULL_CLAIMS] )
Cred Method  = MAX ( fact_credibility_params[METHOD] )

Credibility Z =
    IF ( [Cred Method] = "Bühlmann",
         DIVIDE ( [Exposure Years], [Exposure Years] + [Cred K] ),
         MIN ( 1, SQRT ( DIVIDE ( [Claim Count], [Cred N Full] ) ) ) )

Credibility Level =
    VAR z = [Credibility Z]
    RETURN SWITCH ( TRUE (),
        z >= MAX ( fact_credibility_params[BAND_HIGH] ), "High",
        z >= MAX ( fact_credibility_params[BAND_LOW] ),  "Medium",
                                                         "Low" )

Credible Loss Ratio =
    VAR z = [Credibility Z]
    RETURN z * [Loss Ratio] + ( 1 - z ) * DIVIDE ( [Model Expected Claims], [Earned Premium] )

Credibility Tooltip =
    "Based on " & FORMAT ( [Claim Count], "#,0" ) & " claims and "
        & FORMAT ( [Exposure Years], "#,0" ) & " exposure-years: "
        & FORMAT ( [Credibility Z], "0%" ) & " weight on own experience, "
        & FORMAT ( 1 - [Credibility Z], "0%" ) & " on the model."
```

**One pattern on every page:**
- **Badge:** the same dot (solid, half or hollow) next to every segment name, from
  `[Credibility Level]`.
- **Tooltip:** `[Credibility Tooltip]` on every segment visual.
- **Greying out:** Low rows use a lighter text colour through conditional formatting.
- **Settings panel:** a card from `fact_credibility_params`, e.g. "Method: Bühlmann. K = 76
  exposure-years. A segment needs about 177 exposure-years to reach High." This tells each company
  what its own data supports.

### 9.5 Tests

- Synthetic data: K is estimated (Bühlmann) and the band counts are logged.
- Identical segments (VHM ≤ 0) → classical fallback, with the reason recorded.
- Only one mature year → classical fallback.
- Z is 0 with no exposure, and approaches 1 as exposure grows.
- The DAX `Credibility Z` at segment level matches the Python column.
- No script contains its own credibility threshold.

## 10. What the manager approves (Task 1, decision #2)

1. **Method:** data-driven Bühlmann–Straub, with the classical method as fallback.
2. **Fallback preset:** Strict, **Standard** (proposed) or Lenient.
3. **Bands:** Low < 0.30 ≤ Medium < 0.70 ≤ High.
4. **Blending:** segments are blended with the model's expectation, not hidden.
5. **Decision rule:** Low-credibility segments always need manual review before any action.
