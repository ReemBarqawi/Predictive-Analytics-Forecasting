# Aman Prediction — Task Plan (from 2026-10-22)

> **How to use this file:** do the tasks in order and open **one new session per task**.
> Each task has a ready-made **session prompt**: paste it as the first message of the new session.
> Every session should read `docs/HANDOVER.md` first. Tick a task off here when its "Done when" list is complete.

## Where we are (2026-10-22)

- ✅ **Phase 1 audit done.** Repo reorganised into `pipeline/` and `legacy/`, with hard-coded paths fixed. See `docs/REPO_AUDIT.md`.
- ✅ **Python pipeline runs on the work laptop.** `python run_all.py` (Python 3.13 `.venv`) reproduces all 35 output tables.
- ✅ **Power BI** reads from one `DataFolder` parameter.
- ✅ **Report Server publishing works.** The master report is now **`predicting_insurance_DSS4_RS.pbix`** (classic format). The converter `tools/pbir_to_legacy.py` and the guide `docs/PBIRS_REPORT_FORMAT_GUIDE.md` cover converting other dashboards.
- ⏳ **Data is still synthetic.** The Oracle connection and the business decisions are still open.

## Sequence at a glance

| # | Task | Type | Depends on | Size |
|---|---|---|---|---|
| 0 | Repo housekeeping (commit the master .pbix, merge the branch) | Git | — | XS |
| 1 | Business decisions meeting with the manager | Meeting (you) | — | S |
| 2 | Semantic model cleanup | Power BI | 0 | M |
| 3 | Make every visual Report-Server-safe | Power BI | 2 | M |
| 4 | Central config and pipeline hardening | Python | 1 | M |
| 5 | Oracle data connection and real-data run | Python + data | 1, 4 | L |
| 6 | Rebuild Scenario Simulator and Safe Zone on the optimizer | Power BI + Python | 3, 5 | L |
| 7 | Finish Path to Profitability (5th lever page, glide path, actionable alerts) | Power BI | 3 | M |
| 8 | Forecast page on real data | Python + Power BI | 5 | M |
| 9 | Split into Dashboard A (workbench) and B (executive) | Power BI | 6, 7, 8 | L |
| 10 | Deploy to the company Report Server | IT + you | 9 | L |
| 11 | Future: price elasticity model and embedded chatbot | R&D | 10 | XL |

Tasks 1 and 2 can run in parallel. Task 1 blocks the Python work (4 and 5); task 2 blocks the Power BI work (3, 6 and 7).

---

## Task 0 — Repo housekeeping

**Goal:** git holds exactly what you work on, so every later session starts from a clean, known state.

**Description**
- Commit `predicting_insurance_DSS4_RS.pbix` as the master report.
- Commit the last state of the old `predicting_insurance_DSS4.pbix` as an archive (the data-folder path fix).
- Delete `predicting_insurance_DSS4_before_cleanup.pbix`, or add it to `.gitignore`.
- Merge `docs/phase1-repo-audit` into `main` and push to GitHub.

**Done when:** `git status` is clean on `main`, and GitHub has the converter, the guides and the master .pbix.

**Recommendations**
- **Rename the master** to something stable, e.g. `aman_prediction.pbix`, and archive the PBIR original under `archive/`. Do it in this task, before other sessions start referring to file names.
- **Consider Git LFS** for `.pbix`, `.pkl` and the large CSVs: 20 MB binaries bloat the repo on every save. If you add LFS, do it now, while history is short.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 0 and do it: commit the master .pbix, clean up backups, merge docs/phase1-repo-audit into main. Ask me before renaming files or pushing."*

---

## Task 1 — Business decisions meeting (you + manager)

**Goal:** get the four business decisions that currently block real-data work, so the code stops carrying contradictory numbers.

**Decisions to take** (background in `docs/HANDOVER.md` §12 and `docs/REPO_AUDIT.md` §5):

| # | Decision | Today in the code | Options |
|---|---|---|---|
| 1 | **Target combined ratio** | 0.85 (lever script), 0.95 (optimizer and Power BI), 0.70 loss-ratio target (alerts) | One company target, e.g. 95%, plus an alert threshold |
| 2 | **Credibility rule** | ≥30 claims, ≥300 policies, ≥30 policies, 1,082 claims (4 different rules) | One rule, e.g. classical 1,082 claims for full credibility, with partial credibility below |
| 3 | **Lever baseline** | Levers use all years; Power BI shows by-year | All years, or latest mature year |
| 4 | **Real data access** | — | Oracle host, service name, read-only account, schemas and tables for policies and claims, refresh frequency |

Also confirm with the manager: the motor classes in the real data, the expense loadings (admin 10%, risk margin 5%), and whether the optimizer's retention assumptions are acceptable until real renewal history exists.

**Done when:** each decision is written into `docs/HANDOVER.md` §9 ("Decisions") with the date and who decided.

**Recommendation:** bring the one-page decision table above, and ask for **written** answers (email or Teams). These numbers drive every price recommendation.

*No coding session needed. Afterwards, ask any session to "record these decisions in HANDOVER.md".*

---

## Task 2 — Semantic model cleanup

**Goal:** a lean, understandable model, which makes it faster, easier to hand over, and less likely to break on Report Server.

**Description** (the analysis method is already proven; see `docs/MODEL_CLEANUP.md`):
1. **Re-run the unused-object analysis on the master `_RS` file.** The model has changed since the first analysis (drillthrough edits, visual changes).
2. **Delete what's confirmed unused:** about 210 measures in whole unused folders such as `31 What-If`, `13 Optimizer`, `05 Scenarios`, `02 Pricing`; about 13 tables such as `fact_policy`, `dim_date`, `fact_scenarios`, `fact_kpi_forecast`, `fact_timeseries`, the triangles, and leftover calculated tables; and the `Errors in fact_alerts` query.
3. **Turn off Auto date/time** (File → Options → Current file → Data load). That removes 12 hidden date tables.
4. **Fix the small model issues:**
   - `fact_alerts` loads only 15 of its 17 columns (set `Columns=17`).
   - `PTP Target CR` has no default value.
   - The "Full Cred Standard" table is a leftover from the removed slider.
5. **Refresh, check every page, save, publish to the test server.**

**Done when:**
- the model has no unused measures or tables (re-check shows zero),
- every page renders in Desktop and on the server,
- the file still publishes.

**Recommendations**
- **Back up first** (Save As a copy), and delete in batches: unused folders first, then the scattered measures.
- **Tidy while you're there:** rename display folders consistently (`01 …` to `NN …`, no duplicate `12`/`13`), name pages properly (`Page 1/2/3/4`, `uw`), and add measure descriptions. This is the moment to make the model readable for the CEO dashboard split.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 2 and docs/MODEL_CLEANUP.md. Power BI Desktop for Report Server has predicting_insurance_DSS4_RS.pbix open. Re-run the unused-object analysis via the Power BI modeling connection, show me the list, and after my OK delete in batches."*

---

## Task 3 — Make every visual Report-Server-safe

**Goal:** every page looks and works the same on Report Server as in Desktop.

**Description**
- **Replace the new card visuals (about 34)** with classic **Card** or **HTML Content** cards. They render incompletely in Desktop for Report Server.
- **Check the button slicers (15).**
- **Check the HTML Content visuals in a normal browser.** They looked blank in the built-in test browser, which blocked the custom-visual loader.
- **Fix the Profitability page:** the segment matrix and the side panel were blank on the server, and the slicer panel overlaps the header.
- **Add the alert ACTION column** to the Management Alert panel. This directly answers CEO feedback #4.

**Done when:** a page-by-page check in a normal browser on the test server shows no blank or broken visuals, and the checklist is recorded in the guide.

**Recommendations**
- **Make one reusable KPI card:** a single HTML card measure pattern (value, comparison, coloured delta) reused on every page, instead of 34 separately formatted cards.
- **Pin the toolset:** write down in the guide which visuals are approved for Report Server, so new pages never use unsupported ones.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 3 and docs/PBIRS_REPORT_FORMAT_GUIDE.md section 5. List every new card, button slicer and HTML visual per page in predicting_insurance_DSS4_RS.pbix, propose a replacement for each, then help me replace them page by page."*

---

## Task 4 — Central config and pipeline hardening

**Goal:** the Python pipeline is driven by one config file and contains no demo-only shortcuts, ready for real data (a handover requirement).

**Description**
1. **Create `config.yaml`** (or `config.py`) holding:
   - data and output folders
   - target CR, credibility rule, expense loadings, retention assumptions
   - forecast windows, alert thresholds

   Use the values decided in Task 1. Make every script read it.
2. **Remove hard-coded lists and dates:** the motor class list (in 5 scripts), the `SUM_INSURED` parameters, the `dim_date` range, and the seasonal forecast window. Derive them from the data.
3. **Fix the bugs:**
   - two dead alert rules (`"12-24"` vs `"12->24"`, and `"12_months"` vs `"12m"`)
   - the lever script reading a different baseline than Power BI
4. **Extract shared code** (the feature encoder copied in 4 scripts) into `pipeline/common.py`.
5. **Add basic tests:** the pipeline runs end to end on the synthetic data, and output tables have the expected columns.

**Done when:**
- `python run_all.py` runs with all thresholds coming from config,
- no script contains a hard-coded path, class list or business number,
- the tests pass,
- outputs still match, or the differences are explained by the new decisions.

**Recommendations**
- **Add a run log:** write a small `run_log.csv` (timestamp, git commit, config hash, row counts), so every result in Power BI can be traced to its run.
- **Keep generated outputs out of git:** move the CSVs to a data folder with `.gitignore`. Today about 53 MB of generated CSVs are versioned.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 4, docs/REPO_AUDIT.md §5 and the decisions in docs/HANDOVER.md §9. Plan the central config and the hardening changes for pipeline/, show me the plan, then implement it step by step, keeping run_all.py outputs reconciled."*

---

## Task 5 — Oracle data connection and first real-data run

**Goal:** the pipeline runs on **real company data** from Oracle instead of the synthetic Excel files. This is the core of Phase 2.

> **Update 2026-10-22: the workflow is built and tested** (`docs/DATA_WORKFLOW.md`):
> - `config.toml` plus your private `config.local.toml`
> - `pipeline/00_extract_oracle.py` (with `--check`) and `pipeline/01_prepare_real.py`
> - the column contract `pipeline/contract.py` and the SQL templates in `sql/`
> - `run_all.py --source oracle` and the run log
>
> It was tested end to end on a simulated extract. What remains: fill in the SQL templates and `config.local.toml`, run `--check-connection`, then the first real run (steps 4–5 below).

**Description**
1. **Map the Oracle tables** for policies and claims to the columns the pipeline expects (`pipeline/contract.py` lists them). Write the mapping down.
2. **Add an extract step** that pulls policies and claims from Oracle with a read-only account into the same shape as `fact_policy` and `fact_claim`. Use the `oracledb` package, with credentials kept out of code: environment variables or Windows Credential Manager.
3. **Replace the synthetic builder for real data.** Keep `01_build_synthetic_data.py` for demos. The real path skips the synthetic steps (claim reassignment, fake development, fake sum insured) and computes earned premium, development and segments from real fields.
4. **Run the full pipeline on real data** and reconcile the totals (premium, claims, policy counts) against an official company report.
5. **Point Power BI's `DataFolder`** at the real-data outputs, refresh, and review with the manager.

**Done when:** `run_all.py --source oracle` (or similar) produces all tables from real data, the totals reconcile to a company figure, and the dashboard shows real numbers.

**Recommendations**
- **Start with read-only, date-limited extracts** (e.g. the last 3 years), and check data quality first: match rates, missing fields, open claims.
- **Expect the GLMs and the optimizer to need retuning** on real data. Synthetic results won't carry over.
- **Data privacy:** real policy and claim data must **never** be committed to git or kept on the laptop longer than needed. Add the real-data folder to `.gitignore` before the first extract.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 5 and docs/REPO_AUDIT.md §2–4. I have Oracle access [host/service/schema details]. Help me map the Oracle tables to the pipeline's inputs, build a read-only extract step with credentials outside the code, and run the pipeline on real data."*

---

## Task 6 — Rebuild Scenario Simulator and Safe Zone on the optimizer logic

**Goal:** these two pages use the new **Constrained Retained-Portfolio Profit Optimization** instead of the old 2D premium × claims sliders. This answers CEO feedback #2: optimal scenarios must include claims and retention, not only premium.

**Description**
- **Load the optimizer outputs that aren't in Power BI yet:** `fact_uw_pnl`, `fact_uw_pnl_bridge`, `fact_floor_sensitivity`.
- **Design the repricing flow:** `Profitability Overview` → segment `Recommendation` page (Pages 2–3 today) → `Safe Zone`.
- **Rebuild Scenario Simulator** around the optimizer's results: low/base/high sensitivity, binding constraint, profit bridge.
- **Rebuild Safe Zone** around the break-even and floor sensitivity.
- **Remove the old what-if tables** and measures they no longer use.

**Done when:** both pages read only optimizer tables, the "why this price" story (bridge and binding constraint) is visible, and the manager signs off the flow.

**Recommendation:** decide the **committed-scenario mechanism** now (it's needed for Task 9): for example, a small table where the chosen scenario per segment is recorded. The decision is pre-computed Python scenario bundles, not Power Apps.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 6 and docs/HANDOVER.md §7. Propose the page designs for the new Scenario Simulator and Safe Zone on the optimizer outputs (fact_scenario_results, fact_optimal_scenarios, fact_uw_pnl, fact_uw_pnl_bridge, fact_floor_sensitivity), then help me build them."*

---

## Task 7 — Finish Path to Profitability

**Goal:** the lever story is complete and correct.

**Description**
- **Build the missing Distribution & Mix lever detail page.** The other 4 lever pages exist.
- **Name the lever pages properly** (`Page 1` → Claims Management, `uw` → UW Controls).
- **Verify the CR glide path** reacts correctly to each lever toggle and horizon.
- **Make the realization percentages one source:** they're hard-coded in DAX, so move them to one table that the measures read.
- **Write down each lever's formula and assumptions** for manager validation (the table is in `docs/HANDOVER.md` §5).

**Done when:** all 5 lever pages exist and are named, the glide-path toggles are verified, and the lever assumptions are confirmed by the manager.

**Recommendation:** move the lever formulas from Python into **config** (Task 4), so the manager's numbers change in one place.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 7 and docs/HANDOVER.md §5. Help me build the Distribution & Mix lever page, test the glide path toggles, and move the lever realization percentages into one table."*

---

## Task 8 — Forecast page on real data

**Goal:** a real seasonal forecast. This answers CEO feedback #1: no more "multiplying linearly".

**Description**
- **Retire the remaining flat run-rate forecast** (`08_forecast_kpi_cards.py`) and make sure no visual reads it.
- **Rerun the seasonal forecast** (`07_forecast_seasonal.py`) on real data, with real claims development instead of the demo maturity window.
- **Show forecast accuracy** (backtest WAPE and bias) on the page, so the CEO can see how far to trust it.

**Done when:** the Forecast page uses only the seasonal model on real data, with accuracy shown and the horizons (3/6/12 months) working.

**Recommendation:** install Prophet (optional dependency) once Python access allows. The model comparison then includes all four methods.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 8. Review pipeline/07_forecast_seasonal.py and 08_forecast_kpi_cards.py, retire the linear forecast, and adapt the seasonal forecast to real data."*

---

## Task 9 — Split into Dashboard A and Dashboard B

**Goal:** two audiences, two dashboards (CEO feedback).
- **A — Pricing Workbench:** repricing manager / actuary, detailed and hands-on.
- **B — Executive Portfolio View:** CEO, high level.

They're connected by the **committed-scenario** data layer.

**Description**
- **Agree page lists** for A and B with the manager.
- **Use one shared semantic model**, so both reports read the same model. On Report Server that means one .pbix per report, both refreshing from the same `DataFolder`.
- **Make business-friendly KPI names** (CEO feedback #3).
- **Build the committed-scenario flow:** a scenario committed in A appears in B.

**Done when:** both dashboards are published to the test server, B shows committed scenarios from A, and the CEO and manager each review their own dashboard.

**Recommendation:** keep the Report-Server-safe visual list from Task 3. Copy pages from the master instead of rebuilding them.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 9 and docs/HANDOVER.md §4. Help me design the page split between Dashboard A and B and the committed-scenario data layer, then build both reports."*

---

## Task 10 — Deploy to the company Report Server

**Goal:** the DSS runs on the company server, not on a laptop (a locked decision).

**Description**
1. **Get the server address and access from IT:** Publisher role on a project folder, and the matching *Desktop for Report Server* version.
2. **Data location:** put the outputs where the server can read them (a shared folder or the warehouse), set `DataFolder` to it, and configure data-source credentials and **scheduled refresh** on the server.
3. **Run Python on the server:** clone the repo from GitHub onto the server, create the environment, and schedule `run_all.py` (Windows Task Scheduler) **before** the Power BI refresh.
4. **Permissions:** CEO and executives get the Browser role on Dashboard B; the manager and technical staff get Dashboard A. RLS isn't needed (decision).
5. **Write an operations runbook:** how to rerun, where logs are, who to call.

**Done when:**
- both dashboards are live on the company server,
- the Python run and the Power BI refresh happen on a schedule without your laptop,
- the runbook exists.

**Recommendations**
- **Ask IT for a friendly address** (DNS alias), e.g. `http://amanreports/Reports`.
- **Keep the laptop's local Report Server as the test environment:** publish there first, then to production.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 10. I have [server address, access, shared folder]. Help me plan and execute the deployment: data location, scheduled Python run, Power BI scheduled refresh, permissions, and a runbook."*

---

## Task 11 — Future (not started)

- **Price elasticity model** from historical repricing data. It replaces the optimizer's *assumed* retention curve (k = 3/5/8) with a fitted one. It needs real renewal history, so do it after Task 5.
- **Claude chatbot embedded in Power BI.** Needs an approved API route from the company network, and a decision on what data it may see. Start with a design and security review.

---

## Cross-cutting recommendations

1. **One task, one branch, one PR.** Each session works on its own branch and merges when "Done when" is met.
2. **Never edit the master report in the regular Power BI Desktop.** See `docs/PBIRS_REPORT_FORMAT_GUIDE.md` §6.
3. **Record decisions as you go** in `docs/HANDOVER.md` §9, with date and owner, so later sessions don't re-open them.
4. **State assumptions explicitly.** Every actuarial rule a session proposes should list its assumptions for the manager to validate (handover owner note).
