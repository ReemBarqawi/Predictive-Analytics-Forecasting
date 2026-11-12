"""
18_load_results_db.py - copy finished results into SQL Server for the paginated report
==================================================================================

The paginated alerts report on Report Server reads a SQL Server table, not CSV files. This step copies
the finished results there after the reconcile gate has passed, so the report never shows a run that
failed. Only results are copied: no rule or formula leaves the engine.

    table AlertsOfficial  <- 02_facts/fact_alerts_official.csv   (step 15)

Each table is replaced in one transaction: a report opened during the load sees the old rows or the
new ones, never a mix.

Settings (config.local.toml, per machine):
    [results_db]
    server   = "localhost\\SQLEXPRESS"    # empty or missing: this step does nothing
    database = "AmanResults"             # created if missing
    driver   = "ODBC Driver 18 for SQL Server"
Sign-in is the Windows account that runs the pipeline (no password in any file).

Usage:
    python pipeline/18_load_results_db.py
"""
import sys

import pandas as pd

from settings import S

TABLES = {"AlertsOfficial": "fact_alerts_official.csv"}


def sql_type(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "BIT"
    if pd.api.types.is_integer_dtype(s):
        return "BIGINT"
    if pd.api.types.is_float_dtype(s):
        return "FLOAT"
    return "NVARCHAR(MAX)"


def connect(cfg: dict, database: str | None):
    import pyodbc
    db = f"DATABASE={database};" if database else ""
    return pyodbc.connect(f"DRIVER={{{cfg['driver']}}};SERVER={cfg['server']};{db}"
                          "Trusted_Connection=yes;TrustServerCertificate=yes")


def load_table(conn, name: str, df: pd.DataFrame) -> None:
    cols = ", ".join(f"[{c}] {sql_type(df[c])} NULL" for c in df.columns)
    rows = [tuple(None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in r)
            for r in df.itertuples(index=False)]
    cur = conn.cursor()
    cur.fast_executemany = True
    cur.execute(f"IF OBJECT_ID(N'dbo.[{name}]') IS NOT NULL DROP TABLE dbo.[{name}]")
    cur.execute(f"CREATE TABLE dbo.[{name}] ({cols})")
    if rows:
        marks = ", ".join("?" * len(df.columns))
        cur.executemany(f"INSERT INTO dbo.[{name}] VALUES ({marks})", rows)
    conn.commit()


def main():
    cfg = {"server": "", "database": "AmanResults", "driver": "ODBC Driver 18 for SQL Server"}
    cfg.update(S.config.get("results_db", {}))
    print("=" * 60)
    print("Load results into SQL Server")
    print("=" * 60)
    if not cfg["server"]:
        print("  [results_db] server not set in config.local.toml: nothing loaded")
        return

    with connect(cfg, None) as conn:
        conn.autocommit = True
        conn.execute(f"IF DB_ID(N'{cfg['database']}') IS NULL CREATE DATABASE [{cfg['database']}]")

    with connect(cfg, cfg["database"]) as conn:
        conn.autocommit = False
        for name, file in TABLES.items():
            path = S.facts_dir / file
            if not path.exists():
                sys.exit(f"  {file} not found: run step 15 first")
            df = pd.read_csv(path)
            load_table(conn, name, df)
            print(f"  {len(df)} rows -> {cfg['server']} / {cfg['database']} / dbo.{name}")


if __name__ == "__main__":
    main()
