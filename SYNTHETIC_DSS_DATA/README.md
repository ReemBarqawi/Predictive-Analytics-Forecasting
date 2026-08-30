# Synthetic DSS Dataset — README

> **This is synthetic / demo data engineered from the original demo files.
> It is not real company data and must not be presented as such.**
> The purpose is to demonstrate DSS methodology (Frequency–Severity pricing,
> claims development, profitability, safe zone, scenarios) on a dataset that
> reproduces realistic insurance relationships.

---

## What this delivery is

The original demo files had several structural problems that made them unusable
for a DSS (1.6% policy/claim match rate, 100% open claims in recent years,
premium never joined into the modelling layer, no geographic dimension). This
build takes the original files as inputs, applies documented actuarial
enrichment, and emits a set of clean fact/dimension tables that Power BI and
Python can consume directly.

The originals in `../ORIGINAL_DATA/` are **untouched**. All new content lives
here under `SYNTHETIC_DSS_DATA/`.

## Layout

```
SYNTHETIC_DSS_DATA/
├── 01_source/          modified source-like files (audit trail: ORIGINAL_POLICY_NO preserved)
├── 02_facts/           fact_* tables for Power BI
├── 03_dims/            dim_* tables for Power BI
├── 04_validation/      validation_report.md + supporting CSVs
├── 05_docs/            data dictionary, assumption register, before/after
└── README.md           this file
```

## Key numbers (after the build)

| Metric | Value |
|---|---|
| Policy/claim match rate | 97.0% (was 1.6%) |
| Claim-bearing policies | 2,190 (was 26) |
| Portfolio loss ratio | 95.6% |
| Portfolio combined ratio | 113.7% |
| Zones / cities | 7 / 31 |
| Segments with full credibility | 25 of 34 |
| Total earned premium | SAR 47.4M (reconciles to raw motor total ≈ SAR 47.3M) |
| Preset scenarios | 201 |

## How the DSS pages should be built on these tables

| Power BI page | Primary tables |
|---|---|
| Executive Overview | `fact_segment_performance`, `fact_earned_premium`, `dim_date` |
| Forecast | `fact_earned_premium`, `fact_claim` (+ future `fact_forecast` from Python) |
| Claims Development | `fact_triangle_paid`, `fact_triangle_incurred`, `fact_dev_factors_paid`, `fact_ultimate_by_ay` |
| Profitability | `fact_segment_performance` (LR/ER/CR/UW profit per segment) |
| Scenario Simulator | `fact_scenarios` (bind sliders to `premium_change` / `freq_change` / `severity_change`) |
| Safe Zone & Break-Even | `fact_safe_zone` + `fact_profitability_surface` (matrix heatmap) |
| Zone / Class Explorer | `fact_segment_performance` × `dim_zone` × `dim_business_class` |
| Model Monitoring | to be added when Python forecast/backtest job is written |

## How to rebuild

```
python3 build_synthetic_dss.py
```

The script is fully reproducible (fixed `SEED = 20260829`). Every assumption
(match rate, development curve, expense loading, elasticity, sum insured
distribution, zone mapping) lives at the top of the file and in
`05_docs/assumption_register.md`.

## What must **not** be claimed about this data

1. It is not real company data. Do not present it as such.
2. Loss ratios / combined ratios / profit numbers are functions of synthetic
   assumptions (particularly the paid-to-ultimate curve and the expense loading).
   They can be changed by editing the constants at the top of the build script.
3. Elasticity is an assumption, not an estimate. There is no historical price
   variation in the data to estimate it from.
4. The `CLM_YEAR` trend inside a severity model still confounds inflation and
   claim maturity even after this enrichment — the ULTIMATE_CLAIM column is
   the honest target for severity models, not TOTAL_PAID.
5. ACC_TYPE_DESC remains a retrospective (post-claim) feature. Do not use it
   as a pricing predictor. It is preserved for claims-management / reserving
   analysis only.

## Files an intern would touch first

- `05_docs/before_after.md` — one page showing what changed
- `05_docs/assumption_register.md` — every knob you can turn
- `04_validation/validation_report.md` — every number the build guarantees
- `02_facts/fact_segment_performance.csv` — the DSS's core P&L view
- `02_facts/fact_scenarios.csv` — try filtering to `scenario_name = "Severe Stress"`
