# Pure Premium & Pricing Adequacy — v3

_Per-policy pure premium computed as E[N] × E[S] using the Phase 1 refitted GLMs._

## Portfolio-level view (motor only, positive premium)

| Metric | Value |
|---|---|
| Policies scored | 19,546 |
| Total charged premium | SAR 49,006,634 |
| Total pure premium (period) | SAR 47,175,094 |
| Portfolio adequacy ratio | **1.039** |
| Portfolio position vs pure premium | +3.9% |

Interpretation: an adequacy ratio of 1.00 means the portfolio charges exactly its expected ultimate claim cost. It does NOT include expenses or profit margin — that comparison lives in the break-even work.

## Most underpriced segments (with at least Partial credibility)

| Zone | Class | Type | Policies | Adequacy | vs Pure | Credibility |
|---|---|---|---|---|---|---|
| Central | Motor Commercial | Comprehensive | 587 | 0.505 | -49.5% | Full |
| Northern | Motor Commercial | Comprehensive | 142 | 0.608 | -39.2% | Partial |
| Unspecified | Motor Commercial | Comprehensive | 122 | 0.627 | -37.3% | Partial |
| Western | Motor Commercial | Comprehensive | 719 | 0.645 | -35.5% | Full |
| Eastern | Motor Commercial | Comprehensive | 71 | 0.649 | -35.1% | Partial |

## Most overpriced segments (headroom for competitive pricing)

| Zone | Class | Type | Policies | Adequacy | vs Pure | Credibility |
|---|---|---|---|---|---|---|
| Southern | Motor Commercial | Third Party | 159 | 3.717 | +271.7% | Partial |
| Unspecified | Motor Commercial | Third Party | 57 | 3.712 | +271.2% | Partial |
| Western | Motor Commercial | Third Party | 257 | 3.310 | +231.0% | Partial |
| Central | Motor Commercial | Third Party | 175 | 2.446 | +144.6% | Partial |
| Northern | Motor Commercial | Third Party | 52 | 2.425 | +142.5% | Partial |

## Segment credibility summary

- Full credibility: 14 segments
- Partial credibility: 15 segments
- Low credibility: 5 segments

## Definition-of-done check

| Check | Target | Result |
|---|---|---|
| Per-policy predictions produced | one per motor policy-year | **PASS** (20,871 rows) |
| Predictions vary across policies | severity std > 0 | **PASS** (σ = SAR 1,448) |
| Segment-level statement possible | ≥ 1 Full or Partial credibility segment | **PASS** (29 credible segments) |

## Caveats

1. **Missing SUM_INSURED_BAND** — 416 policies were scored with SUM_INSURED_BAND='Unknown' because their sum insured is missing (intentional data-quality noise in the synthetic build). This matches how Phase 1 trained, so predictions are consistent, but the 'Unknown' bucket does not benefit from sum-insured signal.
2. **Adequacy ≠ profitability** — adequacy compares charged premium to expected ultimate claim cost only. The full profitability picture requires subtracting commission, admin expenses, and a risk margin. That is what `fact_segment_performance.csv` already provides.
3. **Portfolio adequacy < 1.0 does not mean the book is broken** — it means charged premium is below expected ultimate. On a synthetic dataset this is a function of the paid-to-ultimate assumptions in the build script. Change those assumptions and this number moves.
4. **Small segments carry wide uncertainty** — 'Low credibility' rows are directional only. Uncertainty intervals come in Phase 6.
