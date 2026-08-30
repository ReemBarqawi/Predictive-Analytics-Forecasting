# Data Dictionary

Fields added, modified, or preserved during the synthetic enrichment.

Columns are labelled O = original (preserved), M = modified, S = synthetic (new).

## `01_source/policy_bridge_synthetic.csv`

| Column | Origin | Definition | Generation | Business meaning |
|---|---|---|---|---|
| POLICY_NO | O | Policy identifier | source | Primary policy key |
| POLICY_YEAR | O | Underwriting year | source | Renewal year identifier |
| CLASS_DESC | O | Business class | source | e.g. Motor Private, Group Medical |
| POLICY_TYPE_DESC | O | Policy type | source | e.g. Comprehensive, Third Party |
| OFFICE_NAME | O | Sales office | source | Original office string (Arabic/English) |
| POLICY_EFFECTIVE_DATE / POLICY_EXPIRY_DATE | O | Policy period | source | Used for exposure and earned premium |
| TOTAL_PREMIUM / GROSS_PREMIUM / NET_PREMIUM / COMMISSION | O | Premium fields | source, unchanged | Financial base |
| EXPOSURE_DAYS | M | Days in force | expiry − effective | Denominator for frequency modelling |
| EXPOSURE_YEARS | M | Years in force | days / 365.25, clipped 0.01–2.0 | GLM offset |
| CITY | S | City name | mapped from OFFICE_NAME (Arabic/English keywords) | Geographic drill-down |
| REGION | S | Region name | city → region lookup | Higher-level segmentation |
| ZONE | S | 5-zone grouping | region alias | Primary zone for DSS pages |
| SUM_INSURED | S | Sum insured (SAR) | log-normal by POLICY_TYPE_DESC | Ex-ante pricing feature |
| SUM_INSURED_BAND | S | Banded sum insured | binning of SUM_INSURED | Categorical pricing feature |
| POLICY_YEAR_KEY | S | Composite key | POLICY_NO_POLICY_YEAR | Joins per policy-year |

## `01_source/claims_data_synthetic.csv`

| Column | Origin | Definition | Generation | Business meaning |
|---|---|---|---|---|
| CLM_NO | O | Claim number | source | Primary claim key |
| ACC_DATE | O | Accident date | source | Loss occurrence date |
| ACC_TYPE_DESC | O | Accident type | source | Retrospective — NOT a pricing feature |
| TOTAL_PAID | O | Paid to date | source, preserved | Cash paid so far |
| POLICY_NO | M | Reassigned to a valid motor policy | see reassign_claim_policies() | Fixes 1.6% match rate |
| POLICY_YEAR | M | Reassigned in tandem with POLICY_NO | | |
| ORIGINAL_POLICY_NO | S | Preserves original claim POLICY_NO | audit trail | Traceability |
| POLICY_MATCHED | S | True if reassigned successfully | boolean | ~3% intentionally unmatched for DQ demo |
| REPORT_DATE | S | Notification date | ACC_DATE + reporting lag (log-normal) | Reporting lag analysis |
| CLOSE_DATE | M | Settlement date (closed claims only) | ACC_DATE + settlement lag | Development completion |
| IS_CLOSED | S | Closed vs open | probability = CLOSED_FRACTION_BY_CLM_YEAR | Development stage |
| DEV_MONTHS | S | Months of development | (end − ACC_DATE) / 30.44 | Triangle x-axis |
| ULTIMATE_CLAIM | S | Expected final cost | closed → paid × noise; open → paid / paid-to-ult ratio | Cost target for pricing |
| CASE_RESERVE | S | Case reserve (open) | (ultimate − paid) × case share × noise | Reserving |
| INCURRED | S | PAID + CASE_RESERVE | additive | Case-basis view |
| IBNR_COMPONENT | S | ULTIMATE − INCURRED | additive | Incurred-but-not-reported |

## Fact tables

| Table | Grain | Notes |
|---|---|---|
| fact_policy | policy-year | One row per policy-year (all classes) |
| fact_claim | claim | Adds ULTIMATE / CASE_RESERVE / INCURRED / IBNR |
| fact_earned_premium | policy-month | Days-of-cover pro-rata allocation |
| fact_claims_development | AY × dev month | Cumulative paid + incurred + paid-to-ult ratio |
| fact_triangle_paid | AY × dev month | Cumulative paid (Power BI matrix visual) |
| fact_triangle_incurred | AY × dev month | Cumulative incurred |
| fact_dev_factors_paid | dev transition | Chain-ladder factors on paid |
| fact_ultimate_by_ay | AY | Ultimate total per accident year |
| fact_segment_performance | (zone, class, product) | LR, ER, CR, break-even, safe discount |
| fact_profitability | (zone, class, product) | Alias of segment_performance |
| fact_safe_zone | (zone, class, product) | Trimmed safe-zone columns |
| fact_scenarios | scenario | Preset scenarios per segment and portfolio |
| fact_profitability_surface | premium × cost grid | For heatmap on Safe Zone page |

## Dimension tables

- `dim_zone` — office → city → region → zone
- `dim_business_class` — class number to description
- `dim_product` — policy type number to description
- `dim_date` — daily date dimension 2008-01-01 → 2025-12-31
