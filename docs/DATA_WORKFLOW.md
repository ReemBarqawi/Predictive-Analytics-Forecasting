# Data workflow: warehouse → Python → Power BI

> How data flows through Aman Prediction, and how to switch from the demo data to the company
> warehouse. Set up 2026-10-22. Results are written as **CSV files**, and the pipeline runs on
> **the laptop for now** (the company server comes later; see `docs/TASK_PLAN.md` Task 10).

```
 Oracle warehouse ──[0 extract]──▶ data/raw/*.parquet ──[1 prepare]──▶ data/real/02_facts, 03_dims
 (read-only SQL in sql/)            (never in git)                      (same tables as the demo)
                                                                              │
 Demo: ORIGINAL_DATA/*.xlsx ──[1 build synthetic]──▶ SYNTHETIC_DSS_DATA/      │
                                                                              ▼
                                            [2–11 models: GLMs, backtest, optimizer, forecast, alerts]
                                                                              │
                                                                              ▼
                                         Power BI: DataFolder parameter → <output folder>\
```

## 1. One-time setup

1. **Install the packages:** `pip install -r requirements.txt` (adds `oracledb` and `pyarrow`).
2. **Create your private settings file.** Copy `config.local.example.toml` to **`config.local.toml`** and fill in what the DBA gives you:
   ```toml
   [oracle]
   dsn  = "host:port/service_name"
   user = "READONLY_USER"
   ```
   `config.local.toml` is git-ignored. **Never put the password in any file.** Either type it when asked, or set it for the current terminal only:
   ```bash
   set AMAN_ORACLE_PASSWORD=...
   ```
3. **Fill in the SQL templates** `sql/policies.sql` and `sql/claims.sql` with the real schema, table and column names. **Keep the column aliases** (`AS POLICY_NO` etc.): they are the contract the rest of the pipeline relies on. The full column list, required and recommended, is in [`pipeline/contract.py`](../pipeline/contract.py).

## 2. Run

| Command | What it does |
|---|---|
| `python run_all.py --source oracle --check-connection` | Logs in and counts rows only. Nothing is saved. Run this first. |
| `python run_all.py --source oracle` | Extract, prepare, then all models. Results go to `data/real/`. |
| `python run_all.py --source oracle --skip-extract` | Reuses the last extract (e.g. after changing a model) |
| `python run_all.py --source synthetic` | The demo data, exactly as before |
| `python run_all.py --only 9 10` | Re-run single steps (e.g. the optimizer and P&L) |

To make the warehouse the default, put `[run] source = "oracle"` in `config.local.toml`.

## 3. Point Power BI at the results

In Power BI Desktop for Report Server: **Home → Transform data → Edit parameters → `DataFolder`**.

| Data | `DataFolder` value |
|---|---|
| Demo | `C:\Users\<you>\Projects\Predictive-Analytics-Forecasting\SYNTHETIC_DSS_DATA\` |
| Real | `C:\Users\<you>\Projects\Predictive-Analytics-Forecasting\data\real\` |

Then refresh. The real-data tables have exactly the same columns as the demo tables (this is checked), so no query changes are needed.

## 4. What to check after each real-data run

- **`data/real/04_validation/prepare_report.md`** lists:
  - row counts and policy years,
  - the claim-to-policy **match rate** (should be above 90%),
  - columns the warehouse didn't provide,
  - the assumptions applied.
- **`data/run_log.csv`** has one line per run: time, source, status, git version and row counts of the key tables. Use it to trace any dashboard number back to its run.

## 5. Safety rules

- **`data/` and `config.local.toml` are git-ignored.** Real policy and claim data and connection details must never be committed or shared.
- **Use a read-only Oracle account.** The pipeline only runs `SELECT` statements.
- **The extract prints only row counts and column names,** never data values.
- **Keep extracts no longer than needed.** Delete `data/raw/` when it's no longer required.

## 6. Assumptions and open points (agree them with the manager)

| Item | Current setting | Where |
|---|---|---|
| Ultimate claim cost | **Incurred = paid + case reserve, no IBNR** | `config.toml [prepare] ultimate_method` |
| Valuation date | Date of the extract | `config.toml [prepare] as_of_date` |
| Admin expense / risk margin | 10% / 5% | `config.toml [business]` |
| Motor classes | Motor Private, Commercial, Rent a Car | `config.toml [data] motor_classes` (empty list = all classes in the extract) |

### Known limits to address later
- **IBNR / chain-ladder development.** The real path uses incurred cost, so recent accident years will look cheaper than they will end up. A chain-ladder `ultimate_method` is the planned improvement (Task 5).
- **The seasonal forecast has a fixed demo window** (2020-01 to 2023-09) in `07_forecast_seasonal.py`. It must be adapted to real data (Task 8).
- **Earned premium is computed policy by policy.** Fine for tens of thousands of policies, but slow for millions; vectorise it if the real book is large.
- **Business rules are duplicated:** `01_build_synthetic_data.py` still holds its own copy of the expense rates. Move them all into `config.toml` (Task 4).

## 7. How it was tested

- **Demo mode** through the new workflow reproduces all 35 committed tables exactly.
- **Real-data mode** was tested on a **simulated warehouse extract**: only warehouse-style columns, no synthetic ultimate or IBNR fields. Extract → prepare → all 11 steps ran.
  - The claim-to-policy match rate was 97%.
  - All 32 tables Power BI loads came out with identical columns.
  - The test found and fixed a key-type mismatch (`2015` vs `2015.0`) that would have matched 0% of claims on real data.
