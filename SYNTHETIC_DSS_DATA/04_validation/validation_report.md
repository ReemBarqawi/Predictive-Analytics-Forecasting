# Synthetic DSS — Validation Report

_As-of date: 2025-12-31._

| Metric | Value |
|---|---|
| Total policies | 25000 |
| Total motor policies | 20871 |
| Total claims | 5000 |
| Matched claims | 4850 |
| Match rate | 97.0% |
| Unique claim-bearing policies | 2190 |
| Claims-per-policy distribution | {1: 1289, 2: 391, 3: 182, 4: 113, 5: 64, 6: 44, 7: 29, 8: 17, 9: 13, 10: 10, 11: 8, 12: 2, 13: 2, 14: 4, 15: 4, 16: 4, 17: 3, 18: 1, 19: 3, 20: 1, 22: 1, 25: 1, 26: 1, 30: 1, 32: 1, 42: 1} |
| Total gross premium (positive+negative) | SAR 53,502,613 |
| Total motor gross premium | SAR 47,262,587 |
| Total commission | SAR 4,254,035 |
| Total earned premium | SAR 47,396,103 |
| Negative premium rows | 1301 |
| Zero premium rows | 181 |
| Open claim % | 27.3% |
| Severity mean (paid) | SAR 7,584 |
| Severity median (paid) | SAR 3,399 |
| Severity P95 (paid) | SAR 27,652 |
| Severity P99 (paid) | SAR 75,918 |
| Severity max (paid) | SAR 408,000 |
| Total paid (all claims) | SAR 37,920,541 |
| Total ultimate (all claims) | SAR 46,834,138 |
| Ultimate/paid ratio | 1.235 |
| Motor exposure mean | 0.976 years |
| Motor exposure median | 0.966 years |
| Open % by CLM_YEAR | {2008: '1.2%', 2009: '1.1%', 2010: '0.5%', 2011: '1.1%', 2012: '1.1%', 2013: '1.3%', 2014: '0.0%', 2015: '0.0%', 2016: '0.0%', 2017: '0.0%', 2018: '0.0%', 2019: '13.9%', 2020: '21.4%', 2021: '25.4%', 2022: '40.4%', 2023: '61.5%', 2024: '81.3%', 2025: '87.5%'} |
| Zones represented | 7 |
| Business classes represented | 4 |
| Segments with full credibility (>=30 claims) | 25 |
| Segments in Green safe zone | 12 |
| Segments in Yellow safe zone | 5 |
| Segments in Red safe zone | 4 |
| Segments in Loss safe zone | 13 |
| Development factors (paid) | [{'transition': '12->24', 'factor': 1.0669474978605016}, {'transition': '24->36', 'factor': 1.1459560835335516}, {'transition': '36->48', 'factor': 1.086624001307573}, {'transition': '48->60', 'factor': 1.0554319034384692}, {'transition': '60->72', 'factor': 1.0132647184604842}, {'transition': '72->84', 'factor': 1.0006153680906387}, {'transition': '84->156', 'factor': 1.00046092193626}, {'transition': '156->168', 'factor': 1.0041702056751298}, {'transition': '168->180', 'factor': 1.0000949638483425}, {'transition': '180->192', 'factor': 1.0000245643426156}, {'transition': '192->204', 'factor': 1.0000109172174327}, {'transition': '204->216', 'factor': 1.0001163762673273}] |
| Scenario sanity: zero-scenario premium delta | 0.00 |

## Before vs After

| Metric | Before | After | Target | Status |
|---|---|---|---|---|
| Policy/claim match rate | 1.6% | 97.0% | ≥ 90% | ✅ |
| Number of policies | 25,000 | 25,000 | ≈ 25,000 | ✅ |
| Number of claims | 5,000 | 5,000 | ≈ 5,000 | ✅ |
| Overall open claim % | >99% for 2020+ | 27.3% overall | realistic development curve | ✅ |
| Premium available in modelling layer | No (ignored) | Yes (earned + gross) | Yes | ✅ |
| Geographic coverage | BRANCH_NAME = 'Saudi Arabia' only | 7 zones, 31 cities | ≥ 5 zones | ✅ |
| Exposure availability | Days only, no earning | Days + years + policy-month earning | policy-month | ✅ |
| Development structure | None | Triangles + chain-ladder factors + ULTIMATE | usable triangle | ✅ |
| Pure premium capability | Global mean (broken) | Per-segment PP available | per-policy possible | ✅ |
| Profitability capability | None | LR/ER/CR/UW profit per segment | full P&L per segment | ✅ |
| Scenario capability | None | 201 preset scenarios + pure run_scenario() function | callable engine | ✅ |
| Negative premium rows | 1301 | 1301 | preserved for DQ demo | ✅ |
