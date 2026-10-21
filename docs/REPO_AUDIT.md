# Repo Audit — Aman Prediction (Phase 1)

> Audit date: 2026-10-20 · Branch audited: `main` @ `4625ea2` ("last stage")
> Scope: every Python script, every CSV/MD output, the `.pbix` (report definition + TMDL scripts embedded in it).
> **Update 2026-10-20 (after the audit):** scripts were renamed and moved into `pipeline/` and `legacy/`. The tables below still use the
> *pre-rename* names. See the old → new mapping in `HANDOVER.md` §8. Issues #1 (hard-coded script paths) and #2 (builder path bug)
> are fixed in the scripts; the Power BI source paths are not yet changed.
>
> At audit time nothing was modified or re-run. **There is no Python interpreter on the audit machine**, so all findings
> come from reading code and existing outputs, not from executing the pipeline.

---

## 1. File tree

```
Predictive-Analytics-Forecasting/
├── .gitignore                         ignores data/*.xlsx, data/*.csv, outputs/*.csv, __pycache__
├── predicting_insurance_DSS4.pbix     19.9 MB — the single Power BI report (12 pages)
├── ORIGINAL_DATA/                     raw demo extracts (read-only inputs)
│   ├── claims_data.xlsx               ~5,000 claims
│   ├── policy_bridge.xlsx             ~25,000 policy-years (all classes)
│   └── underwriting_data.xlsx         loaded by the builder but not used downstream
├── SYNTHETIC_DSS_DATA/
│   ├── build_synthetic_dss.py         STAGE 1 builder (see §3)
│   ├── zone_mapping.py                OFFICE_NAME → CITY → REGION → ZONE lookup
│   ├── README.md
│   ├── 01_source/                     claims_data_synthetic.csv, policy_bridge_synthetic.csv
│   ├── 02_facts/                      29 fact CSVs + dim_metric.csv  ← Power BI reads from here
│   ├── 03_dims/                       dim_business_class, dim_date, dim_product, dim_zone
│   ├── 04_validation/                 validation_report.md, open_pct_by_year.csv
│   └── 05_docs/                       assumption_register.md, data_dictionary.md, before_after.*
├── models/                            frequency_glm.pkl (NegBin), severity_glm.pkl (Gamma)
├── notebooks/                         all pipeline scripts (plain .py, not notebooks)
├── outputs/                           markdown reports + forecast_validation.png
└── plots/                             EDA / v2-model PNGs (legacy)
```

No `requirements.txt`, no root README, no config file, no tests, no orchestration script.

---

## 2. Script inventory

File names in the repo do **not** match the names used in the handover or in the scripts' own docstrings.
The "Real name" column is the file on disk.

| # | Real name | Docstring / handover name | Status | Inputs | Outputs |
|---|---|---|---|---|---|
| 1 | `notebooks/01_data_loading.py` | — | **Legacy, broken** (reads `data/`, which does not exist) | `data/*.xlsx` | `outputs/claims_cleaned.csv`, `policies_cleaned.csv`, `severity_data.csv`, `claims_by_policy.csv` |
| 2 | `notebooks/02_eda.py` | — | Legacy | outputs of #1 | `plots/01–05_*.png` |
| 3 | `notebooks/03_glm_models.py` | — | Legacy (v2 GLMs; used ACC_TYPE leakage, TOTAL_PAID target) | outputs of #1 | `outputs/*coefficients.csv`, `dashboard_*.csv`, `plots/10_*.png` |
| 4 | `notebooks/04_evaluation.py` | — | Legacy | `outputs/severity_data.csv` | `plots/11–13_*.png` |
| 5 | `notebooks/05_export_for_dashboard.py` | — | Legacy (hard-coded KPI numbers) | outputs of #1 | `outputs/dashboard_*.csv` |
| 6 | `SYNTHETIC_DSS_DATA/build_synthetic_dss.py` | same | **Active, but path bug** (§5 #2) | `ORIGINAL_DATA/*.xlsx` | `01_source/*`, base `02_facts/*` (see §4), `03_dims/*`, `04_validation/*`, `05_docs/*` |
| 7 | `notebooks/05_glm_refit.py` | same | Active | `fact_policy`, `fact_claim` | `models/*.pkl`, `outputs/glm_refit_report.md`, `outputs/*_coefficients_v3.csv` |
| 8 | `notebooks/06_pure_premium.py` | same | Active | `models/*.pkl`, `fact_policy` | **`fact_policy_pricing`**, `outputs/pricing_adequacy_by_segment.csv`, `outputs/pure_premium_report.md` |
| 9 | `notebooks/07_backtest.py` | same | Active (~5–10 min) | `fact_policy`, `fact_claim` | **`fact_model_performance`**, `outputs/backtest_report.md` |
| 10 | `notebooks/08_Build_alerts.py` | — | Active; 2 of 6 rules never fire (§5 #5) | `fact_segment_performance`, `fact_dev_factors_paid`, `fact_policy_pricing`, `fact_model_performance` | **`fact_alerts`** (max 12 rows) |
| 11 | `notebooks/09 underwriting pnl.py` | `09_underwriting_pnl.py` | Active; output **not loaded in Power BI** | `fact_scenario_results`, `fact_optimal_scenarios` | `fact_uw_pnl`, `fact_uw_pnl_bridge` |
| 12 | `notebooks/09_premium_vs_claims.py` | `build_premium_vs_claims_ts.py` | **Superseded** by #13/#15 (linear `np.polyfit` forecast) | `fact_earned_premium`, `fact_claim` | `fact_premium_vs_claims_ts` (not in Power BI) |
| 13 | `notebooks/10_forecast_page.py` | `10_build_forecast_page.py` | Active; KPI forecast = flat run-rate × n quarters | `fact_earned_premium`, `fact_claim`, `fact_dev_factors_paid`, `fact_policy`, `models/*.pkl` | `fact_kpi_forecast`, `fact_timeseries`, `dim_metric` |
| 14 | `notebooks/11_build_synthetic_dss.py` | **`fact_lever_allocation.py`** | Active — this *is* the lever-allocation script | `fact_segment_performance.csv` (all-years file) | **`fact_lever_allocation`** |
| 15 | `notebooks/12_forecast seasonal.py` | same | Active — the seasonal forecast (Prophet / SARIMA / Holt-Winters / seasonal naive) | `fact_earned_premium`, `fact_claim` | **`fact_forecast_seasonal`**, `outputs/seasonal_forecast_report.md`, `outputs/forecast_validation.png` |
| 16 | `notebooks/13_profit optimizer` (no `.py` extension) | **`08_profit_optimizer.py`** | Active — proto-0.3 | `fact_policy_pricing` | **`fact_scenario_results`**, **`fact_optimal_scenarios`**, **`fact_optimizer_run`**, `fact_floor_sensitivity` |
| 17 | `notebooks/14_segment performance by year.py` | `10_segment_performance_by_year.py` | Active | `fact_earned_premium`, `fact_policy_pricing`, `fact_claim` | **`fact_segment_performance_by_year`** |

**Bold** = loaded into Power BI. `outputs/forecast_seasonal_report.md` and `outputs/seasonal_forecast_report.md` look like two versions of the same report.

---

## 3. Data flow and run order

```
ORIGINAL_DATA/*.xlsx
   │
   ▼  [6] build_synthetic_dss.py  (seed 20260829, as-of 2025-12-31)
   │      fact_policy, fact_claim, fact_earned_premium, fact_segment_performance(=fact_profitability),
   │      fact_safe_zone, fact_scenarios, fact_profitability_surface, fact_triangle_*,
   │      fact_dev_factors_paid, fact_ultimate_by_ay, fact_claims_development, dim_*
   │
   ├──▶ [7] 05_glm_refit ──▶ models/frequency_glm.pkl, severity_glm.pkl
   │          │
   │          ├──▶ [8] 06_pure_premium ──▶ fact_policy_pricing ─┬─▶ [16] 13_profit optimizer ──▶ fact_scenario_results,
   │          │                                                  │        fact_optimal_scenarios, fact_optimizer_run,
   │          │                                                  │        fact_floor_sensitivity
   │          │                                                  │            └──▶ [11] 09 underwriting pnl ──▶ fact_uw_pnl, fact_uw_pnl_bridge
   │          │                                                  └─▶ [17] 14_segment performance by year ──▶ fact_segment_performance_by_year
   │          └──▶ [13] 10_forecast_page ──▶ fact_kpi_forecast, fact_timeseries, dim_metric
   │
   ├──▶ [9] 07_backtest ──▶ fact_model_performance
   ├──▶ [14] 11_build_synthetic_dss (levers) ──▶ fact_lever_allocation
   ├──▶ [15] 12_forecast seasonal ──▶ fact_forecast_seasonal
   └──▶ [10] 08_Build_alerts (needs 8 + 9 first) ──▶ fact_alerts
                                   │
                                   ▼
             02_facts/*.csv  ──▶  Power BI (File.Contents on C:\predicting insurance\…)
```

Required run order: **6 → 7 → 8 → 9 → (13, 14, 15, 16, 17 in any order) → 10 → 11**.

---

## 4. Output table schemas (columns as written today)

Grain and key columns for the tables Power BI uses. Full headers are in the CSVs.

| Table | Rows | Grain | Key columns / notes |
|---|---|---|---|
| fact_policy | 25,000 | policy-year (all classes) | POLICY_NO, POLICY_YEAR, CLASS_DESC, POLICY_TYPE_DESC, ZONE, CITY, REGION, TOTAL_PREMIUM, COMMISSION, EXPOSURE_YEARS, SUM_INSURED(_BAND), IS_MOTOR, IS_CANCELLED |
| fact_claim | 5,000 | claim | CLM_NO, POLICY_NO, POLICY_YEAR, ACC_DATE, TOTAL_PAID, ULTIMATE_CLAIM, CASE_RESERVE, INCURRED, IBNR_COMPONENT, IS_CLOSED, DEV_MONTHS, POLICY_MATCHED |
| fact_earned_premium | 265,683 | policy × month | POLICY_NO, POLICY_YEAR, MONTH_DATE, EARNED_PREMIUM, DAYS_IN_MONTH_EXPOSED, ZONE, CLASS_DESC, POLICY_TYPE_DESC |
| fact_policy_pricing | 20,871 | motor policy-year | expected_frequency_per_year/_period, expected_severity, pure_premium_per_year/_period, charged_premium, pricing_adequacy_ratio |
| fact_segment_performance_by_year | 499 | zone × class × type × policy year | EARNED_PREMIUM, ULTIMATE_CLAIMS, CLAIM_COUNT, LOSS/EXPENSE/COMBINED_RATIO, UW_PROFIT, BREAK_EVEN_TOTAL, MAX_SAFE_DISCOUNT, SAFE_ZONE_STATUS, CREDIBILITY_FLAG, CLAIMS_BASIS ("Actual developed" ≤2023, "Model expected" after) |
| fact_lever_allocation | 34 | zone × class × type (all years) | gap_cr_pts, gap_sar, lever_{repricing,bonusmalus,uw_controls,claims_mgmt,distribution}_{cr_pts,sar}, baseline_* |
| fact_scenario_results | 5,673 | segment × case × premium step | premium_change (−10%…+50% in 1% steps), retention, expected_claims/commission/admin, combined_ratio, underwriting_profit, pass_* flags, is_feasible |
| fact_optimal_scenarios | 93 | segment × sensitivity case (low/base/high) | optimal_premium_change, expected_retention, expected_profit, profit_change, binding_constraint, credibility_level, decision_source, nochange_* |
| fact_optimizer_run | 1 | run | every optimizer assumption, stored as strings |
| fact_forecast_seasonal | 66 | month × horizon (3M/6M/12M + ACTUAL) | FORECAST_CLAIMS, FORECAST_PREMIUM, FORECAST_LOSS_RATIO, CI_LOWER_90/UPPER_90, MODEL_TYPE, MODEL_WAPE |
| fact_model_performance | 6,108 | segment × cutoff × horizon × model × target | horizon_label ∈ {2w, 1m, 3m, 12m}, model ∈ {glm, seasonal_naive} |
| fact_alerts | 12 | alert | SEVERITY, TITLE, ISSUE, IMPACT, ACTION, SEGMENT_LABEL, METRIC_NAME/VALUE, ALERT_RULE_ID |

Segment key everywhere: **ZONE × CLASS_DESC × POLICY_TYPE_DESC** (34 combinations on the synthetic data, 7 zones incl. "Other" and "Unspecified").

---

## 5. Issues found (ranked by impact on the real-data phase)

1. **Hard-coded paths to a folder that isn't on this machine.** Power BI loads every table from `C:\predicting insurance\SYNTHETIC_DSS_DATA\…`. Scripts 11, 15, 16 and 17 (the P&L, the seasonal forecast, the optimizer and the by-year table) default to the same path. That folder does not exist here, so the .pbix cannot refresh and those scripts fail without an argument. The other scripts use paths relative to the repo.
2. **`build_synthetic_dss.py` cannot run from where it sits.** It sets `BASE = Path(__file__).parent`, which is now `SYNTHETIC_DSS_DATA/`. It would look for `SYNTHETIC_DSS_DATA/ORIGINAL_DATA/` and write to `SYNTHETIC_DSS_DATA/SYNTHETIC_DSS_DATA/`. It was evidently moved after it was written.
3. **Motor class list is hard-coded** (`["Motor Private", "Motor Commercial", "Motor Rent a Car"]`) in 5 scripts: the builder, `05_glm_refit`, `06_pure_premium`, `07_backtest` and `10_forecast_page`. This conflicts with the requirement that the class list comes from the data. Also hard-coded: `SUM_INSURED` parameters per policy type, the `dim_date` range (2008–2025), and the seasonal-forecast window (2020-01 → 2023-09).
4. **Target CR differs across layers.** The lever script uses `TARGET_CR = 0.85`. The optimizer and the Power BI parameter default use 0.95. The alerts use an LR target of 0.70 (critical at 0.80). Safe-zone bands are 0.90 / 1.00 / 1.10. In DAX, `PTP Target CR` has **no default** (blank when no value is selected), but `AR Target CR` defaults to 0.95. The Path to Profitability DAX recomputes the gap from the slicer and ignores Python's `gap_cr_pts`, but `lever_distribution_sar` is still 10% of a gap measured against 0.85.
5. **Two of the six alert rules never fire** because of key mismatches:
   - `settlement_speed` looks for `"12-24"`, but the data has `"12->24"`.
   - `model_bias` looks for `"12_months"`, but the data has `"12m"`.
6. **Credibility is defined four different ways:** ≥30 / ≥10 claims (segment performance and lever allocation), ≥300 / ≥50 policies (pure premium and backtest), ≥30 policies (optimizer), and `Full Cred Standard` = 1082 claims in Power BI (still in the model as a what-if table). The handover says credibility is "hardcoded", but it doesn't say which of these is meant.
7. **Lever allocation and Power BI read different baselines.** `11_build_synthetic_dss.py` reads `fact_segment_performance.csv`, which covers all years. Power BI's `fact_segment_performance` table actually loads `fact_segment_performance_by_year.csv`, which uses model-expected claims for 2024+. So the lever SAR values and the PTP "current CR" rest on different claims figures. The `aggregate_segments()` dedup currently does nothing, because the all-years file already has one row per segment. It would only matter if the input were switched to the by-year file.
8. **`after_actions_cr` is a placeholder.** It equals `baseline_cr` and `after_actions_sar` is 0. Python deliberately removed the proportional scaling ("DAX handles it"), so the "never overshoot" rule lives only in DAX (`AR Smart Waterfall Bars` / glide measures).
9. **The forecast KPI cards are still linear.** `10_forecast_page.py` holds premium and exposure at the last-4-quarter run-rate and multiplies by n quarters. This is CEO complaint #1, and it is not fully fixed by this script. The Forecast page in Power BI does use `fact_forecast_seasonal` (script 15), so check which visuals still read `fact_kpi_forecast`. The bias constants (−27% … −34%) are copied by hand from `backtest_report.md`.
10. **Optimizer outputs that Power BI doesn't load:** `fact_uw_pnl`, `fact_uw_pnl_bridge`, `fact_floor_sensitivity` (based on the TMDL snapshot, §6).
11. **`fact_alerts` is loaded with `Columns=15`, but the CSV has 17 columns.** The last two (`DETECTED_DATE`, `ALERT_RULE_ID`) are cut off in Power BI.
12. **Reproducibility gaps.** There is no `requirements.txt` (needs pandas, numpy, statsmodels, matplotlib, seaborn, scikit-learn, openpyxl, optional prophet). There is no run-all script. Filenames contain spaces and one has no `.py` extension. Git stores a 20 MB .pbix, about 53 MB of CSVs (fact_earned_premium alone is 265k rows) and 9 MB of pickled models.
13. **The optimizer models only one policy year** (latest = 2024) and uses retention parameters that are assumptions (baseline renewal 0.90, logit slopes 3/5/8). Its own docstring flags them as "never present as calibrated."

---

## 6. Power BI report (`predicting_insurance_DSS4.pbix`)

Read from the embedded PBIR report definition and the TMDL scripts saved in TMDL view (`TMDLScripts/Script 1–7`). The TMDL scripts are snapshots, not the live model. The binary `DataModel` is authoritative and can be checked with the Power BI modeling MCP once Desktop has the file open.

**Model:** 61 tables. That's 28 imported from CSV, 33 calculated or what-if tables (toggles, sliders, `ActionLevers`, `LeverRealization`, `PTP_Timeline`, `dim_case`, …). No relationships appear in the scripts. There are about 437 measures in `'measures table'` across 25 display folders (`01 Portfolio KPIs` … `22 Forecast Page`, `30 Recommendation`, `31 What-If`). No RLS roles.

**Pages (in order):**

| Page | Shown? | Built on | Notes |
|---|---|---|---|
| Forecast | yes | fact_forecast_seasonal, fact_model_performance, dim_metric | Seasonal chart + 8 KPI cards |
| Profitability | yes | fact_earned_premium, credibility measures | Scatter + Deneb + HTML |
| Scenario Simulator | yes | what-if sliders, WaterfallSteps | **Old 2D premium × claims logic** — needs rebuild |
| Safe Zone | yes | what-if sliders, BE measures | **Old logic** — needs rebuild |
| Risk-Based | hidden | fact_lever_allocation, fact_safe_zone | Lever detail 1/5 — Repricing |
| Bonus-Malus | hidden | fact_lever_allocation, BM_ClaimTiers | Lever detail 2/5 |
| uw | hidden | fact_lever_allocation, fact_claim, UW_CostBuildUp | Lever detail 3/5 — UW Controls |
| Path to Profitability | yes | ActionLevers, toggles, PTP_Timeline, Target CR | Lever cards HTML + waterfall + **glide-path line chart already present** (`Glide Quick Wins / Medium Term / Full Plan`) |
| Page 4 | hidden | dims only | Navigation stub (18 buttons) |
| Page 1 | hidden | fact_lever_allocation | Lever detail 4/5 — Claims Management (unnamed) |
| Page 2 | yes | fact_scenario_results, dim_case, RecBridge | New optimizer recommendation (profit/retention curve + bridge) |
| Page 3 | yes | fact_scenario_results | Deneb profit curve (optimizer) |

The **Distribution & Mix** lever detail page doesn't exist yet.
