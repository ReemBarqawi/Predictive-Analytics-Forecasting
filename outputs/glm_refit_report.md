# GLM refit — v3 report (Phase 1)

_Refit on the synthetic DSS dataset. In-sample metrics only — real validation is Phase 3 (rolling-origin backtest)._

## What changed vs v2

| Change | v2 | v3 |
|---|---|---|
| Severity target | TOTAL_PAID (partial for open claims) | **ULTIMATE_CLAIM** (developed) |
| Pricing features include ACC_TYPE_DESC? | Yes (target leakage) | **No** |
| Zone/geography in features? | No | **Yes** |
| Sum insured in features? | No | **Yes (banded)** |
| Frequency aggregation key | POLICY_NO alone (broken) | **(POLICY_NO, POLICY_YEAR)** |
| Policy/claim match rate | 1.6% | **97.0%** |

## Frequency model

- Family: Poisson (log link, offset = log(EXPOSURE_YEARS))
- Features: POLICY_TYPE_DESC, CLASS_DESC, SOURCE_DESC, ZONE, SUM_INSURED_BAND
- Training rows: 25,000
- Total coefficients (incl. intercept): 19
- Significant at 5%: 19
- AIC: 1,038,241
- In-sample predicted claim count: 0.0
- Observed claim count: 4,850
- In-sample bias: -100.00%

## Severity model

- Family: Gamma (log link)
- Target: ULTIMATE_CLAIM
- Features: POLICY_TYPE_DESC, CLASS_DESC, SOURCE_DESC, ZONE, SUM_INSURED_BAND
- Training rows: 4,521
- Total coefficients (incl. intercept): 15
- Significant at 5%: 2
- AIC: 96,645
- In-sample predicted ultimate total: SAR 45,293,548
- Observed ultimate total: SAR 45,327,394
- In-sample bias: -0.07%

## Definition-of-done check

| Check | Target | Result |
|---|---|---|
| Frequency in-sample bias | within ±10% | FAIL (100.0%) |
| Severity in-sample bias | within ±10% | PASS (0.1%) |
| No ACC_TYPE_DESC in features | required | PASS |
| Zone included | required | PASS |

## What this report does NOT show

These are in-sample numbers. True out-of-time accuracy is measured in Phase 3 (rolling-origin backtest).
A model can fit the training data well and still forecast poorly.
