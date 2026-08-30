# Backtest Report — v3 (Phase 3)

_Rolling-origin (expanding window) backtest of the frequency and severity GLMs._
_Predictions are compared against actual claim volumes in the forward window._
_Benchmark: **seasonal naive** — prediction for window (T, T+h] = actuals in (T-1yr, T-1yr+h]._

Cutoffs evaluated: 19
Cutoff range: 2019-12-31 → 2024-06-30

## Portfolio-level performance

### claim_count

| horizon | GLM MAE | Naive MAE | GLM WAPE | Naive WAPE | GLM Bias | Naive Bias | Winner (WAPE) |
|---|---|---|---|---|---|---|---|
| 2w | 17.5 | 19.5 | 64.1% | 71.3% | -33.1% | -4.8% | **GLM** |
| 1m | 34.1 | 39.3 | 63.1% | 72.8% | -27.2% | -3.2% | **GLM** |
| 3m | 97.7 | 113.5 | 60.8% | 70.6% | -26.0% | -3.4% | **GLM** |
| 12m | 380.3 | 407.2 | 54.1% | 57.9% | -32.7% | -6.1% | **GLM** |

### claim_cost

| horizon | GLM MAE | Naive MAE | GLM WAPE | Naive WAPE | GLM Bias | Naive Bias | Winner (WAPE) |
|---|---|---|---|---|---|---|---|
| 2w | SAR 152,462 | SAR 189,322 | 58.2% | 72.3% | -32.4% | -14.1% | **GLM** |
| 1m | SAR 283,777 | SAR 394,892 | 55.4% | 77.1% | -25.8% | -12.7% | **GLM** |
| 3m | SAR 816,970 | SAR 1,113,331 | 51.6% | 70.4% | -27.3% | -10.2% | **GLM** |
| 12m | SAR 3,021,758 | SAR 3,866,905 | 44.0% | 56.3% | -33.8% | -13.8% | **GLM** |

## Segment-level performance (Full credibility segments only)

Segments passing credibility threshold (≥300 policies): 15

### claim_count — per-horizon WAPE comparison

| horizon | GLM WAPE (avg) | Naive WAPE (avg) | Winner |
|---|---|---|---|
| 2w | 61.3% | 81.6% | **GLM** |
| 1m | 59.6% | 83.4% | **GLM** |
| 3m | 60.8% | 77.0% | **GLM** |
| 12m | 56.5% | 64.0% | **GLM** |

### claim_cost — per-horizon WAPE comparison

| horizon | GLM WAPE (avg) | Naive WAPE (avg) | Winner |
|---|---|---|---|
| 2w | 74.1% | 92.6% | **GLM** |
| 1m | 71.0% | 104.7% | **GLM** |
| 3m | 65.9% | 90.1% | **GLM** |
| 12m | 54.2% | 69.5% | **GLM** |

## Definition-of-done check

| Check | Target | Result |
|---|---|---|
| Cutoffs evaluated | ≥ 15 | **PASS** (19) |
| Both models scored | GLM + seasonal naive | **PASS** |
| Portfolio + per-segment output | required | **PASS** |

## How to read this report

**WAPE** (weighted absolute percentage error) = sum(|actual − predicted|) / sum(|actual|). Lower is better. Preferred over MAPE for insurance because it does not blow up when the actual is small or zero.

**Bias** = sum(predicted − actual) / sum(actual). Positive means the model over-predicts on average; negative means under-predicts.

**Winner** compares GLM vs seasonal naive on WAPE. If GLM does not consistently beat naive, that is a genuine finding — write it into the presentation instead of hiding it.

## Caveats

1. **Ultimate cost target** — this backtest uses ULTIMATE_CLAIM (the synthetic best-estimate) as both training target and the actual in the forward window. In a real backtest, the ultimate at cutoff T would be estimated using development factors, not known perfectly.
2. **Short horizons are noisy** — 2-week and 1-month portfolio windows contain small numbers of claims. Point-estimate WAPE on those horizons will be volatile even if the model is sound. The 3-month and 12-month numbers are the ones to lead with.
3. **Segments with fewer than 300 policies are excluded from the segment table** — they lack the credibility to give a meaningful WAPE.
4. **Seasonal naive on this dataset is a strong benchmark** — the synthetic build has stable year-over-year patterns, so beating naive by a large margin is a high bar. Beating it at all is worth stating.
