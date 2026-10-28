# Portfolio Forecast Report

Data source: **synthetic**. Claims data to 2025-01-12.

## Window

- History: 2020-01 to 2023-09 (45 months). Set by forecast_window in config.toml.
- Forecast: 2023-10 to 2024-09.

Earned premium = run-off of policies already written (44% of the forecast year, exact) + new business (forecast written premium, earned with the latest year's policy terms).

## Development to ultimate

Not applied: no claim triangles for this source (claims used as recorded).

## Validation (7 forecast origins, every quarter-end with 24+ months of history)

**7 checks pass, 4 need review.** The same rows are in fact_forecast_validation.csv for Power BI.

| Group | Check | Measure | Horizon | Value | Benchmark | Rule | Status |
|---|---|---|---|---:|---:|---|---|
| accuracy | Error (WAPE) over 7 forecast origins, vs seasonal naive | Earned premium | 3M | 1.8% | 1.8% | at or below seasonal naive | **INFO** |
| accuracy | Bias over 7 forecast origins | Earned premium | 3M | -0.6% |  | within ±10% | **PASS** |
| accuracy | Error (WAPE) over 7 forecast origins, vs seasonal naive | Claim count | 3M | 27.8% | 27.8% | at or below seasonal naive | **INFO** |
| accuracy | Bias over 7 forecast origins | Claim count | 3M | +4.2% |  | within ±10% | **PASS** |
| accuracy | Error (WAPE) over 7 forecast origins, vs seasonal naive | Claims cost | 3M | 33.1% | 33.1% | at or below seasonal naive | **INFO** |
| accuracy | Bias over 7 forecast origins | Claims cost | 3M | -17.9% |  | within ±10% | **REVIEW** |
| accuracy | Error (WAPE) over 6 forecast origins, vs seasonal naive | Earned premium | 6M | 2.1% | 2.1% | at or below seasonal naive | **INFO** |
| accuracy | Bias over 6 forecast origins | Earned premium | 6M | -1.9% |  | within ±10% | **PASS** |
| accuracy | Error (WAPE) over 6 forecast origins, vs seasonal naive | Claim count | 6M | 23.6% | 23.6% | at or below seasonal naive | **INFO** |
| accuracy | Bias over 6 forecast origins | Claim count | 6M | -0.7% |  | within ±10% | **PASS** |
| accuracy | Error (WAPE) over 6 forecast origins, vs seasonal naive | Claims cost | 6M | 30.2% | 30.2% | at or below seasonal naive | **INFO** |
| accuracy | Bias over 6 forecast origins | Claims cost | 6M | -20.4% |  | within ±10% | **REVIEW** |
| accuracy | Error (WAPE) over 4 forecast origins, vs seasonal naive | Earned premium | 12M | 5.4% | 5.4% | at or below seasonal naive | **INFO** |
| accuracy | Bias over 4 forecast origins | Earned premium | 12M | -5.4% |  | within ±10% | **PASS** |
| accuracy | Error (WAPE) over 4 forecast origins, vs seasonal naive | Claim count | 12M | 10.6% | 10.6% | at or below seasonal naive | **INFO** |
| accuracy | Bias over 4 forecast origins | Claim count | 12M | -10.6% |  | within ±10% | **REVIEW** |
| accuracy | Error (WAPE) over 4 forecast origins, vs seasonal naive | Claims cost | 12M | 28.0% | 28.0% | at or below seasonal naive | **INFO** |
| accuracy | Bias over 4 forecast origins | Claims cost | 12M | -28.0% |  | within ±10% | **REVIEW** |
| band | Months inside the 90% band (each origin tested on a band from the others) | Claims cost |  | 95.5% |  | 80% to 97% | **PASS** |
| inputs | Earned premium rebuilt from policy dates / fact_earned_premium | Earned premium |  | 1.000 |  | within ±1% | **PASS** |

### Model choice

| Model | Horizon | Earned premium WAPE | Claim count WAPE | Claims cost WAPE | Claims cost bias |
|---|---|---|---|---|---|
| Seasonal naive | 3M | 1.8% | 27.8% | 33.1% | -17.9% |
| Seasonal naive | 6M | 2.1% | 23.6% | 30.2% | -20.4% |
| Seasonal naive | 12M | 5.4% | 10.6% | 28.0% | -28.0% |
| TimesFM | 3M | 1.3% | 19.3% | 29.5% | -2.6% |
| TimesFM | 6M | 1.7% | 23.9% | 22.2% | -13.3% |
| TimesFM | 12M | 2.5% | 29.9% | 32.8% | -32.8% |

**Selected: Seasonal naive** (lowest mean WAPE over the three measures and horizons).
Actuals for the latest months are themselves developed estimates, and the development factors are today's: the test shows how well the method forecasts the developed book, not what the dashboard would have shown at each origin.

## Next 12 months vs the last 12

| | Last 12 months | Forecast | Change |
|---|---:|---:|---:|
| Earned premium | 5,302,617 | 5,038,676 | -5.0% |
| Claim count | 1,034 | 982 | -5.0% |
| Claims cost | 11,927,840 | 11,325,749 | -5.0% |
| Loss ratio | 224.9% | 224.8% | -0.2 pts |

## Monthly forecast

| Month | Premium | Claims | Claim count | Loss ratio |
|---|---:|---:|---:|---:|
| 2023-10 | 427,020 | 758,364 | 109 | 177.6% |
| 2023-11 | 411,897 | 627,176 | 68 | 152.3% |
| 2023-12 | 422,710 | 899,981 | 96 | 212.9% |
| 2024-01 | 424,101 | 1,317,561 | 87 | 310.7% |
| 2024-02 | 407,103 | 942,602 | 94 | 231.5% |
| 2024-03 | 422,078 | 1,547,439 | 103 | 366.6% |
| 2024-04 | 416,292 | 586,652 | 71 | 140.9% |
| 2024-05 | 420,424 | 875,696 | 74 | 208.3% |
| 2024-06 | 420,357 | 1,135,228 | 88 | 270.1% |
| 2024-07 | 423,467 | 865,317 | 70 | 204.3% |
| 2024-08 | 421,701 | 876,785 | 73 | 207.9% |
| 2024-09 | 421,526 | 892,947 | 49 | 211.8% |
