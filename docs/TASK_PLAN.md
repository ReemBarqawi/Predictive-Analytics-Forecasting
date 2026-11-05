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
| 2 | Semantic model cleanup ⏳ *deletions and folders done; save, fixes and publish open* | Power BI | 0 | M |
| 3 | Make every visual Report-Server-safe | Power BI | 2 | M |
| 4 | Central config and pipeline hardening | Python | 1 | M |
| 5 | Oracle data connection and real-data run | Python + data | 1, 4 | L |
| 6 | Rebuild Scenario Simulator and Safe Zone on the optimizer | Power BI + Python | 3, 5 | L |
| 7 | ~~Finish Path to Profitability~~ **Moved to Future** (Task 11): redesign after the core flow works on real data | — | — | — |
| 8 | Forecast page on real data | Python + Power BI | 5 | M |
| 9 | Split into Dashboard A (workbench) and B (executive) | Power BI | 6, 8 | L |
| 10 | Deploy to the company Report Server | IT + you | 9 | L |
| 11 | Future: price elasticity model and embedded chatbot | R&D | 10 | XL |

Tasks 1 and 2 can run in parallel. Task 1 blocks the Python work (4 and 5); task 2 blocks the Power BI work (3 and 6).

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

**Goal:** get the business decisions that currently block real-data work, so the code stops carrying contradictory numbers.
Decision #5 is parked until the prototype is finished, then reviewed with the manager together with its design file.

**Decisions to take** (background in `docs/HANDOVER.md` §12 and `docs/REPO_AUDIT.md` §5):

| # | Decision | Today in the code | Options |
|---|---|---|---|
| 1 | **Target combined ratio** | 0.85 (lever script), 0.95 (optimizer and Power BI), 0.70 loss-ratio target (alerts) | **Proposed:** a default suggested from the last 5 profitable mature years, which the manager may override, with every value checked against history. The manager approves the rule's parameters and that the target is a **CR, not a loss ratio** (the loss-ratio line in reports = target CR − expense ratio). See `docs/TARGET_CR.md` §8–9 |
| 2 | **Credibility rule** | ≥30 claims, ≥300 policies, ≥30 policies, 1,082 claims (4 different rules) | **Proposed:** one score Z (0–1) estimated from each company's own data (Bühlmann–Straub), with a classical fallback preset, results blended with the model, and High/Medium/Low bands. See `docs/CREDIBILITY.md` §10 |
| 3 | **Baseline** | Levers use all years (2008–2024); the optimizer uses the latest year only; Power BI shows by-year | **Proposed:** one baseline for everything, the *projected no-action CR* for the plan year: the last 3 mature years, premium restated at today's rates, claims trended with the forecast. The optimizer adopts it in Tasks 4–5; the levers use it when Path to Profitability is picked up (Future) |
| 4 | **Real data access** | — | Oracle host, service name, read-only account, schemas and tables for policies and claims, refresh frequency |
| 5 | **Data cut-off, maturity and freshness** ⚠ *review when the prototype is finished* | Typed dates in steps 04, 05, 07; valuation date = extract file date | **Proposed defaults:** cut-off from the data (accident date / policy start), complete-month tests, maturity = 99% earned and 98% paid (actuary to confirm), stale warning after one refresh cycle + 1 month, missing periods never filled with actuals. Also the DBA questions (views stop in early 2025; report and transaction dates). See `docs/DATA_CUTOFF.md` |
| 6 | **Claims development window for the forecast** ⚠ NEEDS MANAGER DECISION *(actuary; review when the prototype is finished)* | `07_forecast_seasonal` chain-ladder, volume-weighted over the latest 20 quarterly diagonals. Its own check (`fact_forecast_validation`, REVIEW) shows the newest accident quarter's cost factor over-stated growth by ~8% on average (7 of 8 past valuations below 1). Effect: Q4 2024 loss ratio 110.6% → ~102%; 2025 forecast loss ratio 100.7% → ~98.7% | Keep 20; shorter window (8–12 diagonals cut the gap to ~3–4%); pick the window from the look-back check each run; or use the company's own reserving factors. Also confirm the forecast pass rules (bias ±10%, band coverage 80–97%, development ±5%) |

| 7 | **Gender and nationality in pricing** ⚠ NEEDS MANAGER DECISION | Not used: pricing runs on the general rating factors only (type, class, source, zone, sum insured band). Switches `use_gender` / `use_nationality` in `config.toml` `[pricing]`, both `false` | Allowed or not (regulation and fairness), per factor. Switching one on also needs the column in the policy extract; the GLM refit stops with a message if it is missing |

| 8 | **Reporting basis for a year's figures** ⏳ IN THE DEMO, NEEDS FINANCE MANAGER APPROVAL *(2026-11-04)* | `[report] year_basis = "written_year"` in `config.toml`: figures count only policies written in the year, their whole premium including what continues after it (2024 = 358.9M on every page). Excluded: 332.5M earned in 2024 by earlier policies still running. Forecast shows policies by start month. Exception: Management Alerts judge actual results, so they follow the same policies only inside the year (172.5M). See `docs/PAGE_RECONCILIATION.md` §8 | **Finance manager to approve or reject.** Finance reports 505.0M earned in calendar 2024, so the dashboard differs on purpose. Approve: empty `year_basis_status`. Reject: set `year_basis = "calendar"` and rerun steps 5, 6, 7, 11, 12. Also review: 2024's 255.0M claims are model-expected, and the model ran 25% below actual over 2024 (122.4M vs 152.4M) |
| 9 | **Alert rules, expected claims and price rule** ⏳ DECIDED FOR THE DEMO, NEEDS MANAGER REVIEW *(2026-11-04)* | Two alert flags (current, future); expected claims = reliable results × bias-corrected trend (1.039); the price is the smallest increase reaching the target within the retention floors; Profitability's 2024 uses the same claims. 2024 moves from +50.6M to −11.6M. See `docs/PAGE_RECONCILIATION.md` §9 | Approve: empty `method_review_status`. Reverse: `expected_claims = "model"` and `selection_rule = "max_profit"` in `config.toml`, rerun steps 5, 6, 7, 9, 10, 11, 12 |
| 10 | **How the Forecast chart shows a year still running** ⏳ IN THE DEMO, NEEDS MANAGER REVIEW *(2026-11-05)* | 2024 claims and loss ratio are estimates (recorded so far 159.5M, shown 317.2M), yet they sit on the chart's Actual line. The tooltip (`powerbi/deneb/forecast_tooltip.vega.json`) marks them Estimate, with the share recorded; a month whose figure is all recorded (Jan) shows Recorded in green, though late claims may still raise it. Each month starts from its recorded claims and the rest of the year's total follows the model's monthly shape (`written_month_view`, step 07). A logic test put 2024 at 281M–353M: recorded claims plus accidents to come, then also late reporting. Jan sits at its recorded floor (28.5M vs 28.7M–33.3M), Apr is ~13% above its range (19.4M), Jul ~5% below (23.5M) | Are three terms (Actual, Estimate, Forecast) clear, or should 2024 claims and loss ratio move to the Forecast line? Is this monthly split acceptable, or should months follow the logic test? Should "Actual developed" stay as the name for 2020–2023, which are recorded claims with no allowance for unreported ones (2023's 67.3% may rise)? |
| 11 | **Claim fields behind these figures** ⚠ NEEDS MANAGER DECISION *(with the DBA, 2026-11-05)* | `RECOVERY_AMT` is not deducted: recorded claims = paid + reserve, and 2021–2023 later recovered 4–6%. `REPORT_DATE` is empty, so reporting delay cannot be measured claim by claim. Only 22 of 326,787 claims are marked closed and almost none have a close date | Should claims be shown before or after recoveries? Can the warehouse supply report and close dates? Until then, do not rely on anything that uses closed status |

Also confirm with the manager: the motor classes in the real data and the expense loadings (admin 10%, risk margin 5%).

**Not a manager decision any more — from the data (user, 2026-10-28):** the optimizer's retention (renewal rate and price sensitivity, today hand-set at 90% and slopes 3 / 5 / 8) is to be estimated from real renewal history. Blocked on a stable renewal link: in `V_UW_DATA` the policy number does not follow the same customer year to year (renewal rate 86.5% for 2022 expiries, 4.7% for 2023; premium change at "renewal" −79% to +326%). Candidates: `PREV_PER_POLICIES` (previous-policy keys) or the vehicle / customer identity in `CARCDE.VCAR_MAX_VEHICLE_ENDT`. The retention floors (80% segment, 85% portfolio) are targets: data default, edited in APEX.

**Done when:** each decision is written into `docs/HANDOVER.md` §9 ("Decisions") with the date and who decided.

**Recommendation:** bring the one-page decision table above, and ask for **written** answers (email or Teams). These numbers drive every price recommendation.

*No coding session needed. Afterwards, ask any session to "record these decisions in HANDOVER.md".*

---

## Task 2 — Semantic model cleanup

> **Status (2026-10-26): ⏳ mostly done, not saved or published yet.** Steps 1–2 and the folder tidy are done in
> `clean model.pbix` (Desktop copy of the master): 224 measures and 14 tables deleted, re-check shows zero unused,
> and the 242 remaining measures are in 11 folders numbered `01`–`11`. Details and the open checklist are in `docs/MODEL_CLEANUP.md`.
> - **Correction to step 4:** `Full Cred Standard` is **in use** (`Credibility Z` reads it). Keep it.
> - **Still open:** save and copy back over the master `_RS` file, Auto date/time off, delete `Errors in fact_alerts`,
>   `fact_alerts` 17 columns, `PTP Target CR` default, stale bookmarks, page names, measure descriptions, publish to the test server.

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
- **Portfolio Outlook (page 1):** the "Loss Ratio Target" slider was bound to an unused `Forecast Target` table, so it didn't move the chart. Rebind it to the `Target Combined Ratio` what-if and label it "Target combined ratio". The chart's loss-ratio line is that CR minus the expense ratio (why CR: `docs/TARGET_CR.md` §9). The Premium-view target now grows with the Annual Growth Rate, like the forecast.
- **Add the alert ACTION column** to the Management Alert panel. This directly answers CEO feedback #4.
  The panel is being rebuilt in Deneb (`powerbi/deneb/management_alerts.vega.json`), and the alert method itself
  is being redesigned: alerts on actual rolling-12-month results, with the model shown alongside. See `docs/ALERTS_METHOD.md`.

**Done when:** a page-by-page check in a normal browser on the test server shows no blank or broken visuals, and the checklist is recorded in the guide.

**Recommendations**
- **Make one reusable KPI card:** a single HTML card measure pattern (value, comparison, coloured delta) reused on every page, instead of 34 separately formatted cards.
- **Pin the toolset:** write down in the guide which visuals are approved for Report Server, so new pages never use unsupported ones.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 3 and docs/PBIRS_REPORT_FORMAT_GUIDE.md section 5. List every new card, button slicer and HTML visual per page in predicting_insurance_DSS4_RS.pbix, propose a replacement for each, then help me replace them page by page."*

---

## Task 4 — Central config and pipeline hardening

**Goal:** the Python pipeline is driven by one config file and contains no demo-only shortcuts, ready for real data (a handover requirement).

**Product rule:** Aman Prediction must run on any insurer's data with no code change. Follow `docs/PRODUCT_RULES.md`: every value is derived from data, a company setting with a data-driven default, or a documented method constant. The full inventory of what to change is in its §4. Path to Profitability items (its §6) are out of scope.

**Description**
1. **Create `config.yaml`** (or `config.py`) holding:
   - data and output folders
   - target CR, credibility rule, expense loadings, retention assumptions
     (target CR: build the design in `docs/TARGET_CR.md` §7 once real data is connected;
     credibility: build `docs/CREDIBILITY.md` §9)
   - forecast windows, alert thresholds

   Use the values decided in Task 1. Make every script read it.
2. **Remove hard-coded lists and dates:** the motor class list (in 5 scripts), the `SUM_INSURED` parameters, the `dim_date` range, and the seasonal forecast window. Derive them from the data.
3. **Fix the bugs:**
   - two dead alert rules (`"12-24"` vs `"12->24"`, and `"12_months"` vs `"12m"`)
   - the lever script reading a different baseline than Power BI (deferred with Path to Profitability)
4. **Extract shared code** (the feature encoder copied in 4 scripts) into `pipeline/common.py`.
5. **Add basic tests:** the pipeline runs end to end on the synthetic data, and output tables have the expected columns.

**Done when:**
- `python run_all.py` runs with all thresholds coming from config,
- no script contains a hard-coded path, class list or business number (`docs/PRODUCT_RULES.md` §5),
- the pipeline runs on a second, differently shaped dataset with no code change,
- the tests pass,
- outputs still match, or the differences are explained by the new decisions.

**Recommendations**
- **Add a run log:** write a small `run_log.csv` (timestamp, git commit, config hash, row counts), so every result in Power BI can be traced to its run.
- **Keep generated outputs out of git:** move the CSVs to a data folder with `.gitignore`. Today about 53 MB of generated CSVs are versioned.

**Session prompt:** *"Read docs/TASK_PLAN.md Task 4, docs/REPO_AUDIT.md §5 and the decisions in docs/HANDOVER.md §9. Plan the central config and the hardening changes for pipeline/, show me the plan, then implement it step by step, keeping run_all.py outputs reconciled."*

---

## Task 5 — Oracle data connection and first real-data run

> **2026-10-27:** the real extract ends in early 2025 (policies 6 Mar 2025, claims 18 Jan 2025), has no report or
> transaction dates, and gives invalid development factors. Cut-off, maturity and freshness design with defaults,
> plus the open questions for the actuary and the DBA: `docs/DATA_CUTOFF.md`.

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

## Task 7 — Path to Profitability: moved to Future

Not done now. The redesign notes are in **Task 11 → Path to Profitability redesign**. Until then, the existing page stays as it is: it is not fixed, extended, or split into Dashboard A/B.

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

**Deployment model:** Option A from `docs/DEPLOYMENT.md` §4: Python plus a Windows scheduled task. Users only open the dashboards; nobody runs Python by hand. The packaged engine (Option B) is for production and comes later (Task 11).

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

### Path to Profitability redesign (next step after the core flow works on real data)

**Where it fits in the flow:** forecast → find risky segments (alerts / Profitability page) → optimizer → explain (what-if, informative only) → safe zone → **commit** the scenario → *if the gap is not closed* → Path to Profitability → monitor actual vs plan.

**Problems found in the current page (review of 2026-10-22):**
1. **Repricing is counted twice.** The optimizer already reprices the segment, and the Repricing lever adds a cruder uplift (adequacy formula, +50% cap, no retention effect). It is 30% of today's lever total.
2. **Different starting point.** The levers start from all years pooled (synthetic CR 113.7%). The optimizer starts from 2024 (112.4%) and ends at 101.6%. Path to Profitability should start at **101.6%**, the post-optimizer CR.
3. **Wrong level for some levers.** Everything is allocated to zone × class × type. Claims management is a portfolio process; distribution and mix works by **channel** (`SOURCE_DESC`).
4. **Flat or circular formulas.** Claims management = 5% of claims; distribution and mix = 10% of the gap (sized by the problem); "UW controls" actually models garage routing, which is claims management.
5. **Overlap and scaling.** Claims-reducing levers are added instead of multiplied; proportional scaling hides whether the gap can really be closed.
6. **Timing too fast.** Price levers show 25% / 90% / 100% by horizon. With annual policies renewing evenly, a rate change reaches about 3% of earned premium after 3 months, 50% after 12 and 100% after 24.

**Redesign:**
- **Repricing lever** = the committed optimizer result (no separate formula).
- **Bonus-malus / no-claims discount** from claim history by years claim-free; close to revenue-neutral, with gains from retention and fewer small claims. Check the regulator's NCD rules.
- **UW controls** = risk-selection rules scored with the GLM (expected claims removed minus premium lost).
- **Claims management** from recoveries, repair-cost and settlement data, applied to the whole portfolio; anything not measurable stays a labelled company setting.
- **Distribution and mix** by channel: commission to target, plus shifting volume to profitable channels and segments; also the "restrict / exit" option for segments the optimizer can't fix.
- **Priority order** instead of proportional scaling, with the remaining gap shown honestly; claims levers multiply.
- **Timing** of price levers from the earning pattern.
- **Levels:** calculated at each lever's natural level, reported at portfolio level, with drill-down to segment and channel.
- All lever values follow `docs/PRODUCT_RULES.md` (§6 lists today's hard-coded ones).

**Prerequisites:** Tasks 5 (real data) and 6 (optimizer pages), the committed-scenario layer (Task 9), the baseline decision (Task 1, #3), and channel data in the extract.

### Packaged engine for production (Option B)

When selling to other insurers, replace Option A with the packaged engine in `docs/DEPLOYMENT.md` §5: one `aman` command-line tool (`check-connection`, `check-data`, `run`, `status`), Python bundled inside, an installer, safe failure that keeps the last good results, results in SQL Server, a refresh trigger, the engine kept apart from client settings for upgrades, versioned models, a licence key and a settings screen.

### Other future items

- **Price elasticity model** from historical repricing data. It replaces the optimizer's *assumed* retention curve (k = 3/5/8) with a fitted one. It needs real renewal history, so do it after Task 5.
- **Claude chatbot embedded in Power BI.** Needs an approved API route from the company network, and a decision on what data it may see. Start with a design and security review.

---

## Cross-cutting recommendations

1. **One task, one branch, one PR.** Each session works on its own branch and merges when "Done when" is met.
2. **Never edit the master report in the regular Power BI Desktop.** See `docs/PBIRS_REPORT_FORMAT_GUIDE.md` §6.
3. **Record decisions as you go** in `docs/HANDOVER.md` §9, with date and owner, so later sessions don't re-open them.
4. **State assumptions explicitly.** Every actuarial rule a session proposes should list its assumptions for the manager to validate (handover owner note).
