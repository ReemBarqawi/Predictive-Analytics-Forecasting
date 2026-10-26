# How Aman Prediction runs at a client

> **Status: agreed 2026-10-25, not implemented yet.**
> - **Now (own company, first pilot): Option A.** Python plus a scheduled task. Built in
>   `docs/TASK_PLAN.md` Task 10.
> - **Production (selling to other insurers): Option B.** A packaged engine with Python bundled
>   inside. A future item in Task 11.
>
> Follows the product rule in `docs/PRODUCT_RULES.md`: the engine is the same for every client,
> and only the client's data mapping and settings differ.

## 1. The principle

**Business users never see or run Python.** At a client, the Python pipeline is a **background
engine on their server that runs on a schedule**. People only open the Power BI dashboards in a
browser.

```
 Client's server (inside their network)
 ┌────────────────────────────────────────────────────────────────┐
 │  Scheduler (e.g. nightly, or monthly after month-end close)    │
 │        │                                                       │
 │        ▼                                                       │
 │  Aman engine = the Python pipeline                             │
 │   1. extract from their database (read-only account)           │
 │   2. prepare + check the data                                  │
 │   3. models: GLM, credibility, optimizer, forecast, alerts     │
 │   4. write results → shared folder / SQL database              │
 │   5. log the run; email IT if it fails                         │
 │        │                                                       │
 │        ▼                                                       │
 │  Power BI Report Server refreshes from those results           │
 └────────────────────────────────────────────────────────────────┘
          │
          ▼
   CEO / pricing manager open the dashboards in a browser
```

## 2. Who does what

| Who | What | How often |
|---|---|---|
| **Us** (vendor / support) | Install the engine, map the client's data, first run, upgrades | Once, then each new version |
| **Client IT** | Provide the server, a read-only database account and a service account; act on failure emails | Once, then on alerts |
| **Client manager / actuary** | Set the company rules (target CR, credibility preset, limits) | When business decisions change |
| **Business users** | Read the dashboards | Daily |

## 3. Packaging options

| Option | What the client gets | Good for | Downside |
|---|---|---|---|
| **A. Python + scheduled task** | A Python environment and the repo; Windows Task Scheduler runs `run_all.py` | **Now:** own company and the first pilot | IT must allow Python on the server; upgrades are manual; the code is visible |
| **B. Packaged engine** | One folder with `aman.exe` (Python bundled inside) and an installer | **Production:** locked-down insurers; no Python install; code not visible | More build work |
| C. Container (Docker) | A container image | Clients with Linux and a container platform | Many on-premises Windows insurers don't run containers |
| D. Hosted by us (SaaS) | Nothing to install | Simplest for clients | Usually blocked: insurer data often has to stay inside the company or the country |

**Decision:** **A now, B in production.** C and D are not planned. D is reconsidered only if
clients accept a cloud hosted inside the country.

## 4. Option A: now (Task 10)

What it needs, on top of today's code (`run_all.py` already has `--source`, `--check-connection`
and the run log):

1. **Server setup:** clone the repo from GitHub, create the `.venv` (Python 3.13),
   `pip install -r requirements.txt`, and create `config.local.toml` (connection details only; the
   password goes in the environment variable or Windows Credential Manager).
2. **Schedule:** a Windows Task Scheduler job runs, under a service account,
   `python run_all.py --source oracle`.
   - **When:** nightly, or monthly after month-end close (agree with the manager).
   - **Order:** the Power BI scheduled refresh runs *after* Python, with a safe time gap.
3. **Results:** written to a folder that Report Server can read. Power BI's `DataFolder`
   parameter points at it.
4. **Failure handling:** check the run log after each run; email IT on failure (a small wrapper
   script around `run_all.py`).
5. **Runbook:** how to rerun, where the logs are, who to call (part of Task 10).

## 5. Option B: production (future)

### 5.1 One command-line tool

Used by client IT and by the scheduler:

| Command | Does |
|---|---|
| `aman check-connection` | Tests the database login |
| `aman check-data` | Checks the extract before any model runs: missing columns, match rates, odd values |
| `aman run` | Runs the full pipeline |
| `aman status` | Last run, duration, row counts, errors |

### 5.2 Engine requirements

1. **Packaged:** Python and all packages bundled into one folder (e.g. PyInstaller or an embedded
   Python), plus an installer. No Python installation on the client's server.
2. **Safe failure:** a failed run keeps the **last good results**, so dashboards never show
   half-finished data. IT gets an email.
3. **Results in a database:** write to SQL Server tables instead of CSV files. Report Server sites
   almost always have SQL Server, and tables are more robust and secure than a shared folder.
4. **Refresh trigger:** the engine triggers the Power BI refresh at the end of a successful run.
   Report Server has a REST API for scheduled refresh plans; confirm it on the client's version.
5. **Separation for upgrades:** the engine (our code) is kept apart from the client's data mapping,
   settings, models and results. A new version replaces only the engine and never touches the
   client's rules.
6. **Model retraining:** re-fit the GLMs monthly or quarterly, not every night. Save each model with
   a version, so every dashboard number traces back to its model and settings.
7. **Licence key:** a check that the installation is licensed, with an expiry date.
8. **Settings screen:** managers set company rules through a small form or a settings table in the
   client's database, with approval and a change history (`docs/PRODUCT_RULES.md` §3), not by
   editing files.

### 5.3 Client folder layout (proposal)

```
C:\AmanPrediction\
  engine\        ← ours: replaced on every upgrade
  config\        ← client: data mapping and company settings
  models\        ← client: fitted models, versioned
  logs\          ← run logs
  (results in the client's SQL Server database)
```

## 6. Security (insurers will ask)

- **Data stays inside:** everything runs in the client's network. No data is sent out.
- **Least access:** the database account is read-only. The password is in the client's credential
  store, never in a file (`docs/DATA_WORKFLOW.md`).
- **Support access:** remote access only when the client allows it, and logs only, not policy
  data.
