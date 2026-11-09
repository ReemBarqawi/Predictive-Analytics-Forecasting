# Pure Premium & Pricing Adequacy — v3

_Per-policy pure premium computed as E[N] × E[S] using the Phase 1 refitted GLMs._

## Portfolio-level view (motor only, positive premium)

| Metric | Value |
|---|---|
| Policies scored | 23,518 |
| Total charged premium | SAR 55,349,912 |
| Total pure premium (period) | SAR 0 |
| Portfolio adequacy ratio | **1187685026546152756831773798473620948309245952.000** |
| Portfolio position vs pure premium | +118768502654615278218478580303820897824331005952.0% |

Interpretation: an adequacy ratio of 1.00 means the portfolio charges exactly its expected ultimate claim cost. It does NOT include expenses or profit margin — that comparison lives in the break-even work.

## Most underpriced segments (with at least Partial credibility)

| Zone | Class | Type | Policies | Adequacy | vs Pure | Credibility |
|---|---|---|---|---|---|---|
| Eastern | Motor Private | Third Party | 390 | 174877645110976690881735981470302170192543744.000 | +17487764511097668850489110604237204238622523392.0% | Full |
| Northern | Motor Private | Third Party | 664 | 176630250571137468215574591813942494132961280.000 | +17663025057113746742329296667129911819752177664.0% | Full |
| Western | Motor Private | Third Party | 3,940 | 184127044369117660861715356153589201203363840.000 | +18412704436911765769258885558301569746160582656.0% | Full |
| Central | Motor Private | Third Party | 3,527 | 216607772955403018777958817377832709564399616.000 | +21660777295540301243970581623668570208088358912.0% | Full |
| Southern | Motor Private | Third Party | 2,409 | 220011974641732039594776672243066552432197632.000 | +22001197464173205068671942424007381552835067904.0% | Full |

## Most overpriced segments (headroom for competitive pricing)

| Zone | Class | Type | Policies | Adequacy | vs Pure | Credibility |
|---|---|---|---|---|---|---|
| Eastern | Group Medical | Individual Medical | 108 | 1735677670054404359950597994040601993027229342717249032120590302253437507995965863944519680.000 | +173567767005440435995059799404060199302722934271724903212059030225343750799596586394451968000.0% | Partial |
| Southern | Group Medical | Individual Medical | 843 | 1525445590177204367647784000967026643959913678186181517117088708054379757869872404538327040.000 | +152544559017720444001783977428964878369177930861612392541082912407973228253086240948403306496.0% | Full |
| Western | Group Medical | Individual Medical | 1,208 | 1302481529303561155557168544678698953162634075921559726105503978413335578914135242667196416.000 | +130248152930356120983471037467066555796153329874401653232580929043234997240987774637647593472.0% | Full |
| Central | Group Medical | Individual Medical | 1,226 | 1109228151700049514563891388573717214454130928539725378060774140991860311519126860827983872.000 | +110922815170004945124009258691642284218874850191352577080375127696967685244076060650049110016.0% | Full |
| Northern | Group Medical | Individual Medical | 540 | 807763343460002064125830743481448783389447534370189989014459146527074306674466030771765248.000 | +80776334346000207769521620097944043458917234007580419056953547453182790504840165669908512768.0% | Full |

## Segment credibility summary

- Full credibility: 17 segments
- Partial credibility: 12 segments
- Low credibility: 6 segments

## Definition-of-done check

| Check | Target | Result |
|---|---|---|
| Per-policy predictions produced | one per motor policy-year | **PASS** (25,000 rows) |
| Predictions vary across policies | severity std > 0 | **PASS** (σ = SAR 1,676) |
| Segment-level statement possible | ≥ 1 Full or Partial credibility segment | **PASS** (29 credible segments) |

## Caveats

1. **Missing SUM_INSURED_BAND** — 4,545 policies were scored with SUM_INSURED_BAND='Unknown' because their sum insured is missing (intentional data-quality noise in the synthetic build). This matches how Phase 1 trained, so predictions are consistent, but the 'Unknown' bucket does not benefit from sum-insured signal.
2. **Adequacy ≠ profitability** — adequacy compares charged premium to expected ultimate claim cost only. The full profitability picture requires subtracting commission, admin expenses, and a risk margin. That is what `fact_segment_performance.csv` already provides.
3. **Portfolio adequacy < 1.0 does not mean the book is broken** — it means charged premium is below expected ultimate. On a synthetic dataset this is a function of the paid-to-ultimate assumptions in the build script. Change those assumptions and this number moves.
4. **Small segments carry wide uncertainty** — 'Low credibility' rows are directional only. Uncertainty intervals come in Phase 6.
