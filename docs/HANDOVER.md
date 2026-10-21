# Project Handover — Aman Prediction (Motor Insurance Pricing DSS)

> Handover context for Claude Code. Read this first before touching any file.
> Owner: Reem Barqawi (Data Analyst, PL-300). Last updated: 2026-10-20 (Phase 1 audit backfill).
> Full file-by-file audit: [`docs/REPO_AUDIT.md`](REPO_AUDIT.md).

---

## 1. Mission

Build a **Motor Insurance Pricing Decision Support System (DSS)** for the **Saudi Arabian market**, called **"Aman Prediction"**.

The system helps a repricing manager and executives make **actuarially-grounded pricing decisions** across three motor lines:
- **Motor Private**
- **Motor Commercial**
- **Motor Rent-a-Car**

> ⚠️ These three lines are only the classes in the **current demo/synthetic data**. The real company data may contain more motor classes, so the line list must come from the data, not the code. **The code doesn't do this yet:** the list is hard-coded in 5 scripts (see §12).

A **Python modeling engine** (GLM pricing + profit optimization) produces the numbers. A **Power BI layer** presents them and lets users explore scenarios.

**Product/showcase name is "Aman Prediction"** (not "AmanDSS").

---

## 2. Where we are right now

- **Data is synthetic/demo**, not real company data yet. Currently rebuilding from raw data (~5k claim rows) with an **edited/widened synthetic range**, as a prototype only.
- **v3** (fixes for the manager's last-demo issues) presented around **2026-10-15**.
- Starting next week: own laptop + database connection → **real-data work begins**. *(Audit note: the current machine has no Python installed, and the folder Power BI points to, `C:\predicting insurance\`, does not exist here.)*
- Architecture decision locked in: **the DSS must NOT run on a local machine**. Python runs in the **cloud / on the company server**, reading from the **company data warehouse** (Oracle SQL). Power BI reads the Python results.
- The project **must work cleanly on real database data**, with no hacks that only hold for demo data.

---

## 3. Architecture / Stack

| Layer | Tech | Role |
|-------|------|------|
| Data source | Oracle SQL (company warehouse) — *today: `ORIGINAL_DATA/*.xlsx`* | Raw policy + claims data |
| Modeling engine | Python (statsmodels GLM, pandas; optional Prophet) | Pricing, scenario bundles, profit optimization |
| Result store | *today: CSVs in `SYNTHETIC_DSS_DATA/02_facts/`* | Pre-computed fact tables |
| Decision layer | Power BI (`predicting_insurance_DSS4.pbix`) | Dashboards, scenario exploration, drill-down |
| Deployment target | Power BI Report Server (on-prem) + company server | Hosting the published reports |

**Flow:** Oracle → Python models (cloud/server) → pre-computed result tables → Power BI reads results.
**Today:** xlsx → `pipeline/01_build_synthetic_data.py` → GLMs → optimizer / levers / forecasts → CSVs → Power BI via `File.Contents("C:\predicting insurance\…")`.

---

## 4. Dashboard structure

> ⚠️ **The 6-page layout below is historical, NOT the final structure.** It's recorded only to explain how the project started.

It started as **one dashboard with 6 pages**: Forecast, Profitability, Scenario Simulator, Safe Zone, Path to Profitability, Risk-Based Repricing (drill-down).

**Current/target structure:** after CEO feedback, it's being split into **TWO dashboards** connected by a **committed-scenario data layer**:

- **Dashboard A — Pricing Workbench** → for the repricing manager / technical actuary (detailed, hands-on).
- **Dashboard B — Executive Portfolio View** → for the CEO (high-level portfolio view).

**Actual state of the .pbix (audit):** still **one** report with 12 pages. 8 are visible: Forecast, Profitability, Scenario Simulator, Safe Zone, Path to Profitability, and Pages 2–3 (the new optimizer recommendation). 5 are hidden: Risk-Based, Bonus-Malus, uw, Page 1 (Claims Mgmt) and Page 4 (navigation stub). The split into A/B and the committed-scenario layer haven't started.

### Stakeholder roles (important)
Only **two realistic roles** exist: **CEO/Executive** and **Repricing Manager/Technical Staff**. Any 7-role layout in reference material is **aspirational, not built**.

**Row-Level Security (RLS) is NOT implemented** (confirmed: no roles in the model).

---

## 5. Path to Profitability page (a major build)

Portfolio-level page (no zone/class filters, **not** per-segment). Key pieces:

- **5 business levers (confirmed):** Risk-Based Repricing, Bonus-Malus, UW Controls, Claims Management, Distribution & Mix.
  The "6" came from the `ActionLevers` waterfall table, which also has two anchor rows ("Starting Point", "Projected Result"). Python, `LeverToggles`, `LeverRealization` and the `Selected Lever` parameter (1–5) all use 5.
- **Proportional smart allocation:** levers share the profitability gap proportionally and **never overshoot** the target. This lives **only in DAX**. Python (`pipeline/06_lever_allocation.py`) writes raw, unscaled lever values (`after_actions_cr` = baseline is a placeholder).
- **3-horizon model:** Quick Wins (0–3mo), Medium Term (3–12mo), Full Plan (12–24mo). Realization % per lever is hard-coded in the `LeverRealization` DAX table:

  | Lever | Quick Win | Medium Term | Full Plan |
  |---|---|---|---|
  | Repricing | 25% | 90% | 100% |
  | Bonus-Malus | 0% | 70% | 100% |
  | UW Controls | 80% | 100% | 100% |
  | Claims Mgmt | 0% | 40% | 100% |
  | Distribution & Mix | 5% | 20% | 100% |

- **ON/OFF toggles per lever:** `Toggle Repricing / Bonus Malus / UW Controls / Claims Mgmt / Mix Shift`. They work alongside the horizon selector.
- **Lever card HTML visual** with dynamic pts/SAR values, powered by **one DAX measure** (`AR Lever Cards HTML`).
- **CR Glide Path line chart: already on the page** (measures `Glide Quick Wins`, `Glide Medium Term`, `Glide Full Plan`, `Glide Target Line`, `Glide After Actions CR`, display folder `12 Glide Path`). Still to confirm: whether all three segments react to the toggles as intended.
- **Lever detail pages: 4 of 5 exist (all hidden).** Repricing ("Risk-Based"), Bonus-Malus, UW Controls ("uw") and Claims Management ("Page 1"). **Distribution & Mix is missing.**
- Target CR comes from the `Target Combined Ratio` what-if (0.80–1.00, default 0.95). `PTP Target CR` has no default and goes blank when nothing is selected (see §12).

### Lever formulas in Python (`pipeline/06_lever_allocation.py`) — assumptions to validate with the manager
| Lever | Rule in code |
|---|---|
| Risk-Based Repricing | If avg premium per policy < pure premium per exposure-year: uplift = 1/adequacy − 1, capped at +50% |
| Bonus-Malus | 15% × claim frequency, capped at 5% of premium |
| UW Controls | 15% severity reduction × 50% realization on ultimate claims |
| Claims Management | 5% of ultimate claims |
| Distribution & Mix | Only for "Loss" segments: 10% of the SAR gap (gap measured against **CR 0.85**) |

---

## 6. CEO / manager feedback (drives current work)

1. **Forecast window just multiplies linearly.** `12_forecast seasonal.py` now produces a seasonal forecast and feeds the Forecast page. However, `10_forecast_page.py` (`fact_kpi_forecast`) still uses flat run-rate × n quarters, and `09_premium_vs_claims.py` uses a linear trend. Make sure no remaining visual reads those.
2. **"Optimal scenario" logic only changes premium, ignoring claims.** Addressed by the optimizer (§7): retention changes the claims and the risk mix.
3. **KPI names not business-friendly.** `dim_metric` adds display names for the forecast KPIs.
4. **Management alerts not actionable.** `08_Build_alerts.py` now writes TITLE / ISSUE / IMPACT / ACTION. 2 of 6 rules never fire (§12).
5. **Heatmap range too narrow.** The Safe Zone page's heatmap uses the `Premium Change` and `Claim Cost Change` tables (Power Query) rather than `fact_profitability_surface`, which covers premium −20%…+20% × claims −20%…+30%. Check the range in those two tables.

---

## 7. The new pricing logic (replacing "Best Scenario")

Design: **"Constrained Retained-Portfolio Profit Optimization"**, a policy-level retention + profit optimizer with sensitivity cases. It **replaces the old "Best Scenario" logic**.

Implemented as (real file names):
- `pipeline/09_profit_optimizer.py` (was `notebooks/13_profit optimizer`), version **proto-0.3**.
- `pipeline/10_underwriting_pnl.py` (was `notebooks/09 underwriting pnl.py`).

How the optimizer works:
- Uses the latest policy year only (2024).
- Tests premium changes from −10% to +50% in 1% steps.
- Retention is a logistic function of the price change: baseline renewal 0.90, slope k = 3 / 5 / 8 for the low / base / high sensitivity cases, and lower-risk policies react more (γ = 0.5).
- Picks the highest-profit price that passes all of these: segment retention ≥ 80%, price within the technical-price cap + 5 pts, and profit ≥ the no-change profit.
- A portfolio repair loop then steps segments down until portfolio retention ≥ 85%. Floor sensitivity is also run at 80 / 82 / 85 / 88%.
- Segments with fewer than 30 policies borrow the price from zone × class, then from class.

All retention parameters are **assumptions, not calibrated** (no renewal history yet).

**Planned repricing workflow:** `Profitability Overview (new)` → drill to a segment-level `Scenario Analysis / recommendation page (new)` → `Safe Zone`.
Pages 2 and 3 in the .pbix are the first version of the recommendation page (they use `fact_scenario_results`, `dim_case`, `RecBridge`).

⚠️ The **old Scenario Simulator and Safe Zone pages** (2D premium × claims what-if sliders) **still need rebuilding** on the new logic. `fact_uw_pnl`, `fact_uw_pnl_bridge` and `fact_floor_sensitivity` are produced but **not loaded** into Power BI yet.

---

## 8. Python files

Full inventory with inputs and outputs: [`REPO_AUDIT.md` §2–4](REPO_AUDIT.md).
On 2026-10-20 the scripts were renamed to match their content, numbered in run order and moved into `pipeline/` (active) and `legacy/` (old). Every active script now finds its folders relative to the repo, so it runs from any machine.

| Run order | File (new name) | Old name | Produces |
|---|---|---|---|
| 1 | `pipeline/01_build_synthetic_data.py` (+ `zone_mapping.py`) | `SYNTHETIC_DSS_DATA/build_synthetic_dss.py` | fact_policy, fact_claim, fact_earned_premium, fact_segment_performance, safe zone, triangles, dev factors, old scenario grid + surface, dims |
| 2 | `pipeline/02_glm_refit.py` | `notebooks/05_glm_refit.py` | `models/frequency_glm.pkl` (NegBin, log-exposure offset), `models/severity_glm.pkl` (Gamma, target ULTIMATE_CLAIM); features: POLICY_TYPE_DESC, CLASS_DESC, SOURCE_DESC, ZONE, SUM_INSURED_BAND |
| 3 | `pipeline/03_pure_premium.py` | `notebooks/06_pure_premium.py` | fact_policy_pricing (per-policy E[N]×E[S], adequacy ratio) |
| 4 | `pipeline/04_backtest.py` | `notebooks/07_backtest.py` | fact_model_performance (rolling-origin, 19 cutoffs, GLM vs seasonal naive) |
| 5 | `pipeline/05_segment_performance_by_year.py` | `notebooks/14_segment performance by year.py` | fact_segment_performance_by_year (**this is what Power BI's `fact_segment_performance` loads**) |
| 6 | `pipeline/06_lever_allocation.py` | `notebooks/11_build_synthetic_dss.py` (the "fact_lever_allocation.py" of earlier notes) | fact_lever_allocation; contains the `aggregate_segments()` dedup + zero-premium drop |
| 7 | `pipeline/07_forecast_seasonal.py` | `notebooks/12_forecast seasonal.py` | fact_forecast_seasonal |
| 8 | `pipeline/08_forecast_kpi_cards.py` | `notebooks/10_forecast_page.py` | fact_kpi_forecast, fact_timeseries, dim_metric |
| 9 | `pipeline/09_profit_optimizer.py` | `notebooks/13_profit optimizer` (a.k.a. "08_profit_optimizer.py") | fact_scenario_results, fact_optimal_scenarios, fact_optimizer_run, fact_floor_sensitivity |
| 10 | `pipeline/10_underwriting_pnl.py` | `notebooks/09 underwriting pnl.py` | fact_uw_pnl, fact_uw_pnl_bridge |
| 11 | `pipeline/11_build_alerts.py` | `notebooks/08_Build_alerts.py` | fact_alerts |

Steps 5–9 only depend on steps 1–3, so they can run in any order. Step 10 needs 9. Step 11 needs 3 and 4.

Legacy, do not run (`legacy/`): `01_data_loading`, `02_eda`, `03_glm_models_v2`, `04_evaluation_v2`, `05_export_for_dashboard` (they read a `data/` folder that no longer exists), and `premium_vs_claims_linear_forecast` (old `09_premium_vs_claims.py`, replaced by 07 and 08).

Key assumptions baked into the code (all values the manager should confirm):
- Admin expense 10% of premium. Risk margin 5%. Target profit margin in the technical premium 5%.
- Break-even = (ultimate × 1.05 + commission) / 0.90.
- Safe zone by combined ratio: Green ≤ 0.90 < Yellow ≤ 1.00 < Red ≤ 1.10 < Loss.
- As-of date 2025-12-31. Policy years after 2023 use model-expected claims (immature).
- Synthetic-only (must disappear with real data): claim→policy reassignment (97% match), closed-claim % by year, paid-to-ultimate curve, case-reserve share 70%, SUM_INSURED drawn from log-normal.

**Scenario saving approach:** **pre-computed Python scenario bundles** (NOT Power Apps) for multi-segment scenario results, which avoids extra licensing cost. Today the "bundle" is `fact_scenario_results` × 3 sensitivity cases.

---

## 9. Decisions & constraints (don't re-litigate these)

- ✅ Pre-computed Python scenario bundles, not Power Apps (licensing).
- ✅ Removed the **Data Confidence slider**; the credibility threshold is now **hardcoded**. *(Audit: the `Full Cred Standard` what-if table, default 1082, is still in the model. See §12 for which threshold is meant.)*
- ✅ No local execution — cloud/server only.
- ✅ Must work on real data, not just synthetic.
- ❌ RLS not implemented.
- Product name: **Aman Prediction**.
- Owner note: **no deep insurance business-domain experience**. When proposing actuarial/insurance rules, state assumptions explicitly and give the reasoning so they can be checked with the manager.

---

## 10. Next steps / roadmap

**PHASE 1 — File audit & structure:** ✅ done 2026-10-20. See `docs/REPO_AUDIT.md`.
Gate: the open items in §12 need a decision from the owner/manager before Phase 2 starts.

**PHASE 2 — Connect the data (next):**
0. *(new, from audit)* Make the pipeline portable: one config for paths and business parameters, `requirements.txt`, a run-all script, no hard-coded class list, fix the builder's path bug.
5. Rebuild from raw data (~5k rows) with widened synthetic range → feed Python models.
6. Establish the Oracle/warehouse connection and confirm the models run on real data (cloud/server, not local).
7. Rebuild old Scenario Simulation + Safe Zone pages on the new optimizer logic.
8. Build the Distribution & Mix lever detail page (the other 4 exist), and confirm the CR Glide Path's toggle behaviour.

**PHASE 3 — Deployment (later):**
9. Research/install **Power BI Report Server (on-prem)**; document requirements, limitations, and an installation guide.
10. Deploy to the **company server**; test by uploading the Power BI Desktop work there.
11. **Clone project files from GitHub** onto the server.

**Future (not started):**
12. **Claude chatbot embedded in Power BI.**
13. **Price elasticity model** from historical repricing data (would replace the optimizer's assumed retention curve).

---

## 11. Terminology cheat-sheet

| Term | Meaning |
|------|---------|
| DSS | Decision Support System (the whole product) |
| Aman Prediction | Product/showcase name |
| Segment | ZONE × CLASS_DESC × POLICY_TYPE_DESC (34 on synthetic data) |
| CR | Combined Ratio = (ultimate claims + commission + admin) / earned premium |
| LR | Loss Ratio = ultimate claims / earned premium |
| Pure premium | Expected claims cost = E[frequency] × E[severity] from the GLMs |
| Technical premium | Pure premium / (1 − commission − admin − target margin) |
| Pricing adequacy | Charged premium / pure premium (< 1 = underpriced) |
| Break-even premium | (Ultimate × 1.05 + commission) / (1 − 10% admin) |
| Max safe discount | 1 − break-even / earned premium |
| Safe zone | Green / Yellow / Red / Loss band by CR (0.90 / 1.00 / 1.10) |
| Credibility | Whether a segment has enough data to trust on its own (see §12: four definitions in use) |
| Ultimate claim | Final expected cost of a claim after development (paid + case reserve + IBNR) |
| IBNR | Incurred but not reported: ultimate − incurred |
| Dev factor | Chain-ladder age-to-age factor on cumulative paid (e.g. `12->24`) |
| Retention | Probability a policy renews at a given price change (assumed logistic) |
| Sensitivity case | low / base / high customer price sensitivity in the optimizer |
| Binding constraint | Why the optimizer stopped at its chosen price (retention floor, technical price, price cap, profit peak) |
| No-change | 0% price change baseline, on the same retention basis as the recommendation |
| Lever | A business action that moves profitability (repricing, bonus-malus, UW controls, claims mgmt, distribution & mix) |
| Horizon | Quick Wins (0–3m), Medium Term (3–12m), Full Plan (12–24m) |
| Glide Path | Trajectory of CR across the 3 horizons |
| Dashboard A | Pricing Workbench (manager/actuary) |
| Dashboard B | Executive Portfolio View (CEO) |
| Committed-scenario layer | Data link connecting Dashboard A → Dashboard B (not built) |

---

## 12. Open items to confirm

Resolved by the audit:
- [x] Exact repo structure / file tree → `docs/REPO_AUDIT.md` §1.
- [x] Definitive lever list → **5** (§5).

Still open (owner / manager decision needed):
- [ ] Requirements file (to be uploaded): align this handover to it.
- [ ] Oracle connection details + warehouse schema for the real-data phase.
- [ ] **One target CR.** The lever script uses 0.85. The optimizer and Power BI default use 0.95. Alerts use an LR target of 0.70.
- [ ] **One credibility rule.** Today: ≥30 claims (segments), ≥300 policies (pure premium/backtest), ≥30 policies (optimizer), 1082 claims (Power BI what-if).
- [ ] **Lever baseline.** Should levers use all years (as now) or the by-year table that Power BI's `fact_segment_performance` uses?
- [ ] **Repoint Power BI sources** from `C:\predicting insurance\` to this repo (work laptop: `C:\Users\rbarqawi\Projects\Predictive-Analytics-Forecasting`), ideally through one `DataFolder` parameter. The scripts are already fixed (relative paths).
- [ ] Bugs to fix at the start of Phase 2 (details in `REPO_AUDIT.md` §5):
  - the builder's path bug
  - 2 dead alert rules (`"12-24"` vs `"12->24"`, `"12_months"` vs `"12m"`)
  - `fact_alerts` loaded with 15 of 17 columns
  - `PTP Target CR` has no default
  - optimizer P&L tables not loaded into Power BI
