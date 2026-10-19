# Seasonal Forecast Report

Generated: 2026-10-04 12:26

> **DEMO FORECAST:** Synthetic data. Modelling window excludes immature Oct–Dec 2023.

## Data Summary

- Window: 2020-01 to 2023-09 (45 months)
- Train: 39, Holdout: 6
- Total claims: 3,021

## Model Comparison

| Model | WAPE | Bias | Coverage | MAE |
|---|---:|---:|---:|---:|
| Prophet | 38.2% | 1.382 | 83% | 352,187 |
| SARIMA | 33.5% | 1.335 | 100% | 309,081 |
| HoltWinters | 97.3% | 1.973 | 0% | 898,019 |
| Naive | 43.0% | 0.845 | — | 396,977 |

**Selected:** SARIMA — Lowest WAPE among candidates with reasonable validation bias (0.65–1.35).

## Seasonality

- ANOVA F=0.29, p=0.9841
- Not significant at 5%

## Business Validation

- Trailing 12M avg monthly premium: 443,109
- Forecast avg monthly premium: 426,544
- Premium change: -3.7%
- Trailing 12M LR: 224.3%
- Forecast 12M LR: 215.0%

## Validation Summary

- Aggregate WAPE: 33.5% PASS
- Monthly WAPE: 33.5%
- Raw bias: 1.335
- Coverage: 100%

## 12-Month Forecast Preview

| Month | Claims | Premium | LR | CI Lower | CI Upper |
|---|---:|---:|---:|---:|---:|
| 2023-10 | 983,946 | 426,990 | 230.4% | 53.0% | 407.9% |
| 2023-11 | 1,027,479 | 413,600 | 248.4% | 57.6% | 439.3% |
| 2023-12 | 949,887 | 434,774 | 218.5% | 29.9% | 407.1% |
| 2024-01 | 960,643 | 434,919 | 220.9% | 25.6% | 416.1% |
| 2024-02 | 1,016,238 | 386,363 | 263.0% | 35.9% | 490.2% |
| 2024-03 | 895,958 | 436,455 | 205.3% | 0.0% | 412.6% |
| 2024-04 | 940,745 | 428,161 | 219.7% | 2.1% | 437.3% |
| 2024-05 | 864,749 | 445,909 | 193.9% | 0.0% | 408.7% |
| 2024-06 | 829,889 | 427,498 | 194.1% | 0.0% | 424.0% |
| 2024-07 | 830,894 | 440,415 | 188.7% | 0.0% | 417.4% |
| 2024-08 | 893,817 | 435,108 | 205.4% | 0.0% | 442.5% |
| 2024-09 | 809,471 | 408,342 | 198.2% | 0.0% | 456.6% |

