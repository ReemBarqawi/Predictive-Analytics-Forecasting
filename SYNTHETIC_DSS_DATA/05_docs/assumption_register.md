# Assumption Register

Every synthetic assumption used in the build. All values are configurable in `pipeline/01_build_synthetic_data.py`.

| Assumption | Value | Reason | Impact | Configurable |
|---|---|---|---|---|
| Random seed | 20260829 | Reproducibility | Deterministic outputs | Yes |
| As-of date | 2025-12-31 | Cut-off for maturity of open claims | Sets what is closed vs open | Yes |
| Target match rate | 97% | Fix the 1.6% join collapse | Drives frequency-model usability | Yes |
| Unmatched kept for realism | 3% | Preserve a real-world DQ signal | Feeds DQ monitoring page | Yes |
| Closed % by claim year | see dict | Development pattern by maturity | Determines open %, controls IBNR uplift | Yes |
| Paid-to-ultimate ratio for open claims | see dict | Synthesise ultimate cost from partial payments | Drives ULTIMATE_CLAIM values | Yes |
| Case-reserve share of remaining | 70% | Split ULTIMATE minus PAID into case reserve vs IBNR | Case reserves vs IBNR balance | Yes |
| Admin expense rate | 10% | Standard industry loading | Combined ratio and break-even | Yes |
| Risk margin | 5% | Prudence loading on expected claims | Break-even premium level | Yes |
| Assumed elasticity (default) | 0.0 | No historical price variation to estimate elasticity | Scenario premium/exposure interaction | Yes |
| Sum insured (Comprehensive) — log-normal μ,σ | μ=11.2,σ=0.55 | Realistic sum-insured spread for pricing features | Feature added to pricing dataset | Yes |
| Sum insured (Third Party) — log-normal μ,σ | μ=9.9,σ=0.4 | Realistic sum-insured spread for pricing features | Feature added to pricing dataset | Yes |
| Data-quality missing rate | 2% | Small realistic gap in SUM_INSURED for DQ demo | Feeds DQ page | Yes |
| Zone mapping | keyword → city → region | Convert OFFICE_NAME into 5-zone geographic dimension | Enables segmentation, safe-zone by zone | Yes (in pipeline/zone_mapping.py) |
