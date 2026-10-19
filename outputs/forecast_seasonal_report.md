# Seasonal Forecast Validation Report

Generated: 2026-09-27 13:22

## Summary

- Total segments: 33
- Prophet selected: 9
- SARIMA selected: 21
- Portfolio blend (fallback): 3

## Validation Results

- Segments passing ALL checks: 0 / 33 (0%)

### Holdout metrics (n=30 evaluated segments)

- WAPE: median 241.8%, mean 850.8%
  - Pass (<35%): 0 / 30
- Bias: median 0.755, mean 7.316
  - Pass (0.90–1.10): 0 / 30
- 90% CI Coverage: median 66.7%, mean 61.7%
  - Pass (>=85%): 5 / 30

## Segment Detail

| Zone | Class | Policy Type | Months | Model | WAPE | Bias | Coverage | Pass |
|------|-------|-------------|--------|-------|------|------|----------|------|
| Central | Motor Commercial | Comprehensive | 48 | Prophet | 120.7% | -0.026 | 0% | FAIL |
| Central | Motor Commercial | Third Party | 48 | SARIMA | 834.8% | 9.348 | 100% | FAIL |
| Central | Motor Private | Comprehensive | 48 | SARIMA | 337.4% | 4.329 | 83% | FAIL |
| Central | Motor Private | Third Party | 48 | SARIMA | 1235.2% | 13.352 | 83% | FAIL |
| Central | Motor Rent a Car | Comprehensive | 48 | SARIMA | 76.5% | 0.627 | 83% | FAIL |
| Eastern | Motor Commercial | Comprehensive | 48 | Prophet | 100.0% | 0.000 | 67% | FAIL |
| Eastern | Motor Commercial | Third Party | 45 | SARIMA | 10583.9% | 106.839 | 100% | FAIL |
| Eastern | Motor Private | Comprehensive | 48 | SARIMA | 135.5% | 0.564 | 50% | FAIL |
| Eastern | Motor Private | Third Party | 48 | SARIMA | 1983.1% | 12.763 | 83% | FAIL |
| Eastern | Motor Rent a Car | Comprehensive | 48 | SARIMA | 1458.2% | 15.582 | 83% | FAIL |
| Northern | Motor Commercial | Comprehensive | 48 | SARIMA | 90.0% | 0.883 | 50% | FAIL |
| Northern | Motor Commercial | Third Party | 48 | Prophet | 710.7% | -6.107 | 67% | FAIL |
| Northern | Motor Private | Comprehensive | 48 | SARIMA | 255.0% | 3.142 | 67% | FAIL |
| Northern | Motor Private | Third Party | 48 | SARIMA | 596.0% | 6.960 | 67% | FAIL |
| Northern | Motor Rent a Car | Comprehensive | 48 | SARIMA | 280.4% | 2.158 | 67% | FAIL |
| Other | Motor Commercial | Third Party | 13 | Portfolio_Blend | — | — | — | FAIL |
| Other | Motor Private | Comprehensive | 25 | Portfolio_Blend | — | — | — | FAIL |
| Other | Motor Private | Third Party | 24 | Portfolio_Blend | — | — | — | FAIL |
| Southern | Motor Commercial | Comprehensive | 48 | Prophet | 91.8% | 0.572 | 0% | FAIL |
| Southern | Motor Commercial | Third Party | 48 | SARIMA | 80.4% | 1.232 | 100% | FAIL |
| Southern | Motor Private | Comprehensive | 48 | SARIMA | 101.1% | 0.615 | 67% | FAIL |
| Southern | Motor Private | Third Party | 48 | SARIMA | 232.3% | 2.406 | 33% | FAIL |
| Southern | Motor Rent a Car | Comprehensive | 48 | SARIMA | 4057.1% | 41.571 | 83% | FAIL |
| Unspecified | Motor Commercial | Comprehensive | 48 | Prophet | 111.2% | -0.112 | 0% | FAIL |
| Unspecified | Motor Commercial | Third Party | 48 | Prophet | 132.1% | 0.261 | 33% | FAIL |
| Unspecified | Motor Private | Comprehensive | 48 | SARIMA | 115.5% | 0.364 | 83% | FAIL |
| Unspecified | Motor Private | Third Party | 48 | SARIMA | 287.4% | 3.217 | 100% | FAIL |
| Unspecified | Motor Rent a Car | Comprehensive | 48 | Prophet | 111.9% | -0.022 | 0% | FAIL |
| Western | Motor Commercial | Comprehensive | 48 | Prophet | 251.3% | -0.703 | 0% | FAIL |
| Western | Motor Commercial | Third Party | 48 | SARIMA | 464.1% | 3.188 | 100% | FAIL |
| Western | Motor Private | Comprehensive | 48 | SARIMA | 73.8% | 0.411 | 67% | FAIL |
| Western | Motor Private | Third Party | 48 | SARIMA | 506.5% | -4.065 | 83% | FAIL |
| Western | Motor Rent a Car | Comprehensive | 48 | Prophet | 108.8% | 0.135 | 50% | FAIL |

## Methodology

- **Prophet**: multiplicative seasonality, Hijri holidays (Ramadan, Eid al-Fitr, Eid al-Adha, Hajj), changepoint_prior_scale=0.05
- **SARIMA**: SARIMA(1,1,1)(1,1,1,12) as benchmark
- **Selection**: lower WAPE on 6-month holdout wins
- **Bias correction**: multiplicative (actual_mean / predicted_mean) on trailing 12 months
- **Fallback**: segments with <24 months use portfolio-level Prophet with credibility blend
- **Validation gates**: WAPE < 35%, Bias in [0.9, 1.1], 90% CI coverage >= 85%
- Failing segments fall back to credibility-blended portfolio forecast with wider CI and a flag