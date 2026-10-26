# Target combined ratio (CR): design

> **Status: designed 2026-10-22, not implemented yet.** Build it when the pipeline is connected to
> real data (`docs/TASK_PLAN.md` Tasks 4–5). The rule's parameters are the manager's decision
> (Task 1, decision #1). Record the approved values in `docs/HANDOVER.md` §9.

## 1. The problem

Today the target CR is a different number in each place:

| Where | Value | How it is set |
|---|---|---|
| `pipeline/06_lever_allocation.py` | 0.85 | hard-coded `TARGET_CR` |
| `pipeline/09_profit_optimizer.py` | 0.95 | `ASSUMPTIONS["target_combined_ratio"]` |
| Power BI `Target Combined Ratio` what-if | 0.80–1.00, default 0.95 | slider |
| Power BI `PTP Target CR` | no default | goes blank when nothing is selected |

The two simple fixes both fail:

- **A fixed number** goes stale, and only someone who edits code can change it.
- **A free entry field** lets the manager type a value without knowing what the company has
  actually achieved, for example 70% when no recent year was below 120%.

There is also a structural problem. **Report Server cannot run Python when a slider moves.** Python
runs on a schedule and Power BI reads what it wrote. Anything Python computes *using* the target is
frozen at the value of that run, so a manager's slider change would not reach it.

## 2. The solution in one paragraph

The system **suggests** a target from the company's own history. The manager **may override** it.
Every value, suggested or entered, is **checked against history** and labelled (Realistic, Stretch,
Never achieved, Loss-making). **Python never compares anything to the target:** it writes only
target-free facts plus the suggestion. **Power BI does every comparison in DAX** from one
`[Target CR]` measure, so a slider change updates everything instantly.

## 3. The suggested default (data-driven)

Computed by Python on every run, from `fact_segment_performance_by_year.csv` totalled to portfolio
level per policy year:

1. **Mature years only.** Use years with `CLAIMS_BASIS = "Actual developed"`. Recent years whose
   claims are still model estimates are left out.
2. **Look-back window.** The last **5** mature years.
3. **Credible years only.** A year needs credibility Z ≥ 0.30 (`docs/CREDIBILITY.md`; until that is built, at least **30 claims**).
4. **Profitable years only.** CR **below 100%**.
5. **Suggested target** = premium-weighted CR of the years that pass 1–4:
   `Σ(claims + commission + admin) / Σ earned premium`.
   CR is always recomputed from totals, never averaged.
6. **Keep it realistic.** Clip the result to the band **80%–100%**.
7. **Fallback.** If no year passes, use the company policy value **95%** and say so.

**Why not "the best year"?** One lucky year sets a target nobody can repeat. On the synthetic data
the only profitable year in 2019–2023 is 2019 at **46.8%**, while 2023 was **123.4%**. A "best
year" rule would suggest 47%. The rule above suggests 80% (clipped) and labels it **Stretch**.

Every suggestion carries a plain-language **basis**, e.g. *"Premium-weighted CR 46.8% of
profitable year 2019, kept inside 80%–100%."*

## 4. The check (applies to any value)

A **hit** is a credible year in the window whose CR was at or below the target.

| Label | Rule | Meaning for the manager |
|---|---|---|
| ✅ Realistic | hits ≥ 2 | The company reached it in at least 2 of the last 5 years. |
| ⚠️ Stretch | hits = 1 | Reached once. Possible, but needs real action. |
| ❌ Never achieved | hits = 0 | No recent year got there. Expect the levers not to close the gap. |
| 🔴 Loss-making | target ≥ 100% | The plan accepts an underwriting loss. |

Each label comes with a sentence, e.g. *"Target 93.0% was reached in 1 of the last 5 credible
years; latest year 2023 was 123.4%."*

A **typo guard** rejects any official value outside **60%–110%** (catches `93` instead of `0.93`).
The Power BI slider range does the same job for what-if values.

## 5. Two levels of target

| | Official target | What-if target |
|---|---|---|
| Who sets it | Manager, approved in writing | Anyone exploring the dashboard |
| Where | Config (`manager_value`); `0` = use the suggestion | Power BI slider |
| Changes | Rarely; takes effect on the next scheduled run | Instantly |
| Used by | The pipeline's suggestion and check rows; the slider's default | Every DAX comparison |

The page always shows both when they differ, e.g. **"Exploring 90% (official target 93%, Realistic)"**,
so nobody mistakes a what-if for the company target.

## 6. Rule: Python calculates, Power BI compares

Python writes results that **do not depend on the target**. Power BI compares them to `[Target CR]`.

What uses the target today, and where it moves:

| Step | Today | Change | Why it is safe |
|---|---|---|---|
| 06 levers | `gap_cr = max(0, CR − TARGET_CR)`; levers set to 0 when the gap is 0; the Distribution lever is 10% of `gap_sar` | Write raw lever amounts and the baseline CR/SAR only. Gap, the "already profitable → 0" rule and the Distribution lever move to DAX. | Lever amounts 1–4 don't use the target. The "never overshoot" scaling is already in DAX. |
| 09 optimizer | `remaining_profitability_gap_pts = max(0, CR − target) × 100`, added after optimizing | Drop the column. DAX computes it from `combined_ratio`. | The optimal price maximises profit within the retention limits; the target plays no part in the choice. |
| 11 alerts | Safe-zone rules use break-even and fixed CR bands (90/100/110%). LR target 0.70 is a separate setting. | No change to these rules. Any **"above target"** status or colour is a DAX measure. | Alert text stays in Python; the target comparison doesn't. |

The **loss-ratio target (0.70)** used by alerts is a different measure from the target CR. It stays
its own config setting.

**Exception (future).** If a calculation ever needs the target *inside* Python (for example an
optimizer constraint "segment CR ≤ target"), pre-compute a grid: run it for each target from 80% to
100% in 1-point steps, store a `TARGET_CR` column, and let the slider filter to the matching rows.

## 7. Implementation spec (for when real data is connected)

### 7.1 Config (`config.toml`)

```toml
[business.target_cr]
manager_value      = 0             # [MANAGER DECISION] official target; 0 = use the suggestion
lookback_years     = 5
profitable_below   = 1.00
min_claims         = 30            # align with the credibility decision (Task 1, #2)
min_years_achieved = 2             # "Realistic" threshold
realistic_range    = [0.80, 1.00]
hard_limits        = [0.60, 1.10]  # typo guard for manager_value
fallback           = 0.95
```

### 7.2 Python

- **New module `pipeline/target_cr.py`.** Reads the settings above and the by-year table.
  Returns `suggested`, `basis`, `official` (manager value or suggestion), and the check label and
  note for each. Stops the run with a clear message if `manager_value` breaks the typo guard.
- **Call it at the end of step 5** (it needs `fact_segment_performance_by_year.csv`) to write
  `fact_target_cr.csv`.
- **Steps 6 and 9:** remove `TARGET_CR` and `target_combined_ratio` and every column derived from
  them, as in §6.
- **Run log:** add the official target and its source to `data/run_log.csv`.

### 7.3 Output `02_facts/fact_target_cr.csv`

| Column | History rows (one per year in the window) | `Suggested` row | `Official` row |
|---|---|---|---|
| `ROW_TYPE` | `History` | `Suggested` | `Official` |
| `POLICY_YEAR` | year | blank | blank |
| `COMBINED_RATIO` | that year's CR | suggested value | official value |
| `EARNED_PREMIUM`, `CLAIM_COUNT` | year totals | blank | blank |
| `CREDIBLE`, `PROFITABLE` | flags | blank | blank |
| `SOURCE` | blank | `Data` / `Fallback` | `Manager` / `Suggested` |
| `STATUS`, `NOTE` | blank | label + basis | label + note |

### 7.4 Power BI

Load `fact_target_cr` from the `DataFolder`. Keep **one** what-if table, `Target CR`
(`GENERATESERIES(0.60, 1.10, 0.01)`), and delete `PTP Target CR` (or point it at the measure below).

```DAX
Official Target CR =
    CALCULATE ( MAX ( fact_target_cr[COMBINED_RATIO] ), fact_target_cr[ROW_TYPE] = "Official" )

Target CR =                                   -- the only target every measure uses
    SELECTEDVALUE ( 'Target CR'[Value], [Official Target CR] )

Target CR Hits =                              -- credible years at or below the target
    CALCULATE (
        COUNTROWS ( fact_target_cr ),
        fact_target_cr[ROW_TYPE] = "History",
        fact_target_cr[CREDIBLE] = TRUE (),
        fact_target_cr[COMBINED_RATIO] <= [Target CR]
    ) + 0

Target CR Status =
    SWITCH ( TRUE (),
        [Target CR] >= 1,      "Loss-making",
        [Target CR Hits] >= 2, "Realistic",
        [Target CR Hits] = 1,  "Stretch",
                               "Never achieved" )

Target CR Label =
    IF ( ISFILTERED ( 'Target CR'[Value] ) && [Target CR] <> [Official Target CR],
         "Exploring " & FORMAT ( [Target CR], "0%" ) & " (official "
             & FORMAT ( [Official Target CR], "0%" ) & ")",
         "Official target " & FORMAT ( [Target CR], "0%" ) )
         & " · " & [Target CR Status]

Gap to Target (pts) =
    MAX ( 0, [Combined Ratio] - [Target CR] ) * 100
```

- The "Target CR Hits" threshold (2) must match `min_years_achieved` in config. Read it from a
  config row in `fact_target_cr` if it changes often.
- Point the lever, glide-path and optimizer-gap measures at `[Target CR]`.
- **Next to the slider:** a card with `[Target CR Label]` coloured by status, and a small column
  chart of the History rows with `[Target CR]` as a line. This shows the manager the history while
  choosing.

### 7.5 Tests

- Suggestion: only a profitable outlier year → clipped and labelled Stretch (the synthetic case).
- No profitable credible year → fallback 95%, basis says so.
- `manager_value = 93` → run stops with the typo message.
- `manager_value = 1.02` → Loss-making.
- Steps 6 and 9 outputs contain no target-dependent columns.

## 8. What the manager approves (Task 1)

The manager approves the **rule**, not only a number:

1. Look-back window: **5** mature years.
2. Profitable means CR **below 100%**.
3. Credible year: at least **30** claims (align with credibility decision #2).
4. Realistic band for the suggestion: **80%–100%**.
5. Fallback when no year qualifies: **95%**.
6. "Realistic" means reached in at least **2** of the window's years.
7. Official target: **use the suggestion**, or a specific value (e.g. 93%).

With the rule approved, the suggestion updates itself each year as new data matures, and the
official number only changes when the manager decides to.
