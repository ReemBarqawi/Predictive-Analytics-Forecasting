# Seasonal Forecast Report

Generated: 2026-10-26 08:52

> **DEMO FORECAST:** Synthetic data. Modelling window excludes immature Oct–Dec 2023.

## Data Summary

- Window: 2020-01 to 2023-09 (45 months)
- Train: 39, Holdout: 6
- Total claims: 3,021

## Model Comparison

| Model | WAPE | Bias | Coverage | MAE |
|---|---:|---:|---:|---:|
| SARIMA | 33.5% | 1.335 | 100% | 309,081 |
| HoltWinters | 97.4% | 1.974 | 0% | 898,712 |
| Naive | 43.0% | 0.845 | — | 396,977 |

**Selected:** SARIMA — Lowest WAPE among candidates with reasonable validation bias (0.65–1.35).

## Seasonality

- ANOVA F=0.29, p=0.9841
- Not significant at 5%

## Business Validation

- Trailing 12M avg monthly premium: 441,885
- Forecast avg monthly premium: 424,506
- Premium change: -3.9%
- Trailing 12M LR: 224.9%
- Forecast 12M LR: 216.0%

## Validation Summary

- Aggregate WAPE: 33.5% PASS
- Monthly WAPE: 33.5%
- Raw bias: 1.335
- Coverage: 100%

## 12-Month Forecast Preview

| Month | Claims | Premium | LR | CI Lower | CI Upper |
|---|---:|---:|---:|---:|---:|
| 2023-10 | 983,946 | 424,966 | 231.5% | 53.2% | 409.9% |
| 2023-11 | 1,027,479 | 411,428 | 249.7% | 57.9% | 441.6% |
| 2023-12 | 949,887 | 432,518 | 219.6% | 30.1% | 409.2% |
| 2024-01 | 960,643 | 432,783 | 222.0% | 25.8% | 418.2% |
| 2024-02 | 1,016,238 | 384,189 | 264.5% | 36.1% | 492.9% |
| 2024-03 | 895,958 | 434,568 | 206.2% | 0.0% | 414.4% |
| 2024-04 | 940,745 | 426,464 | 220.6% | 2.1% | 439.1% |
| 2024-05 | 864,749 | 443,895 | 194.8% | 0.0% | 410.5% |
| 2024-06 | 829,889 | 425,485 | 195.0% | 0.0% | 426.0% |
| 2024-07 | 830,894 | 438,596 | 189.4% | 0.0% | 419.1% |
| 2024-08 | 893,817 | 433,046 | 206.4% | 0.0% | 444.6% |
| 2024-09 | 809,471 | 406,138 | 199.3% | 0.0% | 459.0% |

