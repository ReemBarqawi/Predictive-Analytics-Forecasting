"""
profile_dtw.py: read-only catalog of the DTWCDE (data warehouse) schema: every table and view, its size,
columns, comments, date range and which source tables a view reads. Only metadata, counts and
min/max dates come back, never policy, customer or claim rows.

    .venv/Scripts/python.exe tools/profile_dtw.py                  # metadata only, seconds
    .venv/Scripts/python.exe tools/profile_dtw.py --scan           # + exact row counts and date ranges
    .venv/Scripts/python.exe tools/profile_dtw.py --scan --timeout 120 --owner CARCDE   # another schema

Connection: same as pipeline/00_extract_oracle.py (config.local.toml + AMAN_ORACLE_PASSWORD or prompt).
Writes <raw_dir>/checks/ (git-ignored), named after the schema (dtwcde_*.csv):
  <owner>_objects.csv        tables / views / materialized views: rows (statistics), columns, date columns,
                             comment, last analyzed; with --scan also exact rows, first and last date
  <owner>_columns.csv        every column: type, fill share and distinct values (statistics), comment
  <owner>_dependencies.csv   for each view: the tables and views it reads, in any schema
  <owner>_dates.csv          with --scan: min / max of each date column of each object
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from settings import S  # noqa: E402

_spec = importlib.util.spec_from_file_location("extract", ROOT / "pipeline" / "00_extract_oracle.py")
_extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_extract)

OBJECTS_SQL = """
SELECT o.OBJECT_NAME, o.OBJECT_TYPE, o.STATUS, o.CREATED, o.LAST_DDL_TIME,
       t.NUM_ROWS, t.LAST_ANALYZED,
       (SELECT MAX(cm.COMMENTS) FROM ALL_TAB_COMMENTS cm
         WHERE cm.OWNER = o.OWNER AND cm.TABLE_NAME = o.OBJECT_NAME) AS COMMENTS,
       (SELECT COUNT(*) FROM ALL_TAB_COLUMNS c
         WHERE c.OWNER = o.OWNER AND c.TABLE_NAME = o.OBJECT_NAME) AS N_COLUMNS,
       (SELECT COUNT(*) FROM ALL_TAB_COLUMNS c
         WHERE c.OWNER = o.OWNER AND c.TABLE_NAME = o.OBJECT_NAME
           AND (c.DATA_TYPE = 'DATE' OR c.DATA_TYPE LIKE 'TIMESTAMP%')) AS N_DATE_COLUMNS
FROM   ALL_OBJECTS o
LEFT   JOIN ALL_TABLES t ON t.OWNER = o.OWNER AND t.TABLE_NAME = o.OBJECT_NAME
WHERE  o.OWNER = '{owner}' AND o.OBJECT_TYPE IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
  -- a materialized view is also listed as a TABLE of the same name: keep the MV row only
  AND  NOT (o.OBJECT_TYPE = 'TABLE' AND EXISTS (
            SELECT 1 FROM ALL_MVIEWS m WHERE m.OWNER = o.OWNER AND m.MVIEW_NAME = o.OBJECT_NAME))
ORDER  BY o.OBJECT_TYPE, o.OBJECT_NAME
"""

COLUMNS_SQL = """
SELECT c.TABLE_NAME, c.COLUMN_ID, c.COLUMN_NAME, c.DATA_TYPE, c.DATA_LENGTH, c.NULLABLE,
       c.NUM_DISTINCT, c.NUM_NULLS, t.NUM_ROWS, cc.COMMENTS
FROM   ALL_TAB_COLUMNS c
LEFT   JOIN ALL_TABLES t ON t.OWNER = c.OWNER AND t.TABLE_NAME = c.TABLE_NAME
LEFT   JOIN ALL_COL_COMMENTS cc
       ON cc.OWNER = c.OWNER AND cc.TABLE_NAME = c.TABLE_NAME AND cc.COLUMN_NAME = c.COLUMN_NAME
WHERE  c.OWNER = '{owner}'
ORDER  BY c.TABLE_NAME, c.COLUMN_ID
"""

DEPS_SQL = """
SELECT NAME AS VIEW_NAME, TYPE AS VIEW_TYPE, REFERENCED_OWNER, REFERENCED_NAME, REFERENCED_TYPE
FROM   ALL_DEPENDENCIES
WHERE  OWNER = '{owner}' AND TYPE IN ('VIEW', 'MATERIALIZED VIEW')
  AND  REFERENCED_TYPE IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW', 'SYNONYM')
  AND  REFERENCED_OWNER NOT IN ('SYS', 'PUBLIC')
ORDER  BY 1, 3, 4
"""


def query(conn, sql: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql)
    return pd.DataFrame(cur.fetchall(), columns=[d[0].upper() for d in cur.description])


def scan(conn, owner: str, objects: pd.DataFrame, columns: pd.DataFrame, timeout_s: int):
    """Exact row count and min / max of every date column, one query per object, each stopped after
    timeout_s seconds. Objects whose statistics say 0 rows are skipped."""
    conn.call_timeout = timeout_s * 1000
    dates, extra = [], []
    todo = objects[objects["NUM_ROWS"].fillna(-1) != 0]
    for i, o in enumerate(todo.itertuples(index=False), 1):
        name = o.OBJECT_NAME
        dcols = columns[(columns["TABLE_NAME"] == name)
                        & columns["DATA_TYPE"].str.match(r"DATE|TIMESTAMP")]["COLUMN_NAME"].tolist()
        sel = ", ".join(["COUNT(*) AS N"] + [f'MIN("{c}") AS "MN{j}", MAX("{c}") AS "MX{j}"'
                                             for j, c in enumerate(dcols)])
        print(f"  {i}/{len(todo)} {name} ({o.OBJECT_TYPE.lower()}, {len(dcols)} date columns)...", end=" ", flush=True)
        try:
            r = query(conn, f'SELECT {sel} FROM "{owner}"."{name}"').iloc[0]
        except Exception as e:  # timeout, privileges or an invalid view: note it and go on
            msg = str(e).splitlines()[0][:100]
            print(f"skipped: {msg}")
            extra.append(dict(OBJECT_NAME=name, ROWS_EXACT=None, SCAN_NOTE=msg))
            try:
                conn.ping()
            except Exception:
                print("      connection lost; reconnecting")
                conn = _extract.connect()
                conn.call_timeout = timeout_s * 1000
            continue
        print(f"{int(r['N']):,} rows")
        mins = [r[f"MN{j}"] for j in range(len(dcols))]
        maxs = [r[f"MX{j}"] for j in range(len(dcols))]
        for c, mn, mx in zip(dcols, mins, maxs):
            dates.append(dict(OBJECT_NAME=name, COLUMN_NAME=c, MIN_DATE=mn, MAX_DATE=mx))
        # the object's span: earliest and latest of its date columns, ignoring placeholder years
        ok = lambda d: d is not None and pd.notna(d) and 1990 <= pd.Timestamp(d).year <= 2100  # noqa: E731
        extra.append(dict(OBJECT_NAME=name, ROWS_EXACT=int(r["N"]),
                          FIRST_DATE=min((d for d in mins if ok(d)), default=None),
                          LAST_DATE=max((d for d in maxs if ok(d)), default=None), SCAN_NOTE=""))
    return pd.DataFrame(extra), pd.DataFrame(dates), conn


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--owner", default="DTWCDE", help="schema to catalog (default DTWCDE)")
    ap.add_argument("--scan", action="store_true",
                    help="also count rows and read date ranges (one query per object)")
    ap.add_argument("--timeout", type=int, default=60, help="seconds per object for --scan")
    args = ap.parse_args()
    owner = args.owner.upper()

    out_dir = (S.raw_dir or ROOT / "data" / "raw") / "checks"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = owner.lower()
    conn = _extract.connect()
    print(f"Connected (Oracle {conn.version}). Read-only: metadata, counts and date ranges only.")

    objects = query(conn, OBJECTS_SQL.format(owner=owner))
    if objects.empty:
        sys.exit(f"No tables or views visible in {owner} for this user.")
    objects = objects.drop_duplicates("OBJECT_NAME")
    columns = query(conn, COLUMNS_SQL.format(owner=owner)).drop_duplicates(["TABLE_NAME", "COLUMN_NAME"])
    columns["SHARE_FILLED"] = 1 - columns["NUM_NULLS"] / columns["NUM_ROWS"]
    deps = query(conn, DEPS_SQL.format(owner=owner))
    print(f"  {len(objects)} objects, {len(columns)} columns, {len(deps)} view dependencies")

    if args.scan:
        extra, dates, conn = scan(conn, owner, objects, columns, args.timeout)
        objects = objects.merge(extra, on="OBJECT_NAME", how="left")
        dates.to_csv(out_dir / f"{stem}_dates.csv", index=False)
    conn.close()

    # source schemas per view, as one readable cell on the object list
    src = (deps.assign(SRC=deps["REFERENCED_OWNER"] + "." + deps["REFERENCED_NAME"])
               .groupby("VIEW_NAME")["SRC"].agg(lambda s: "; ".join(sorted(set(s)))))
    objects["READS_FROM"] = objects["OBJECT_NAME"].map(src).fillna("")
    objects.to_csv(out_dir / f"{stem}_objects.csv", index=False)
    columns.to_csv(out_dir / f"{stem}_columns.csv", index=False)
    deps.to_csv(out_dir / f"{stem}_dependencies.csv", index=False)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 60)
    print(f"\n{owner} objects by type:")
    print(objects.groupby(["OBJECT_TYPE", "STATUS"]).size().to_string())
    rows = "ROWS_EXACT" if "ROWS_EXACT" in objects else "NUM_ROWS"
    rows_any = objects[rows].fillna(objects["NUM_ROWS"]) if rows == "ROWS_EXACT" else objects[rows]
    objects["ROWS"] = rows_any
    show = ["OBJECT_NAME", "OBJECT_TYPE", "ROWS", "N_COLUMNS"] + \
           (["FIRST_DATE", "LAST_DATE"] if args.scan else []) + ["COMMENTS"]
    print("\nLargest objects:")
    print(objects.sort_values("ROWS", ascending=False, na_position="last")[show].head(40)
          .to_string(index=False, na_rep="-"))
    empty = objects[objects["ROWS"] == 0]["OBJECT_NAME"]
    print(f"\nEmpty ({len(empty)}): {', '.join(empty) or 'none'}")
    if not deps.empty:
        print("\nSchemas the views read from:")
        print(deps.groupby("REFERENCED_OWNER")["VIEW_NAME"].nunique().sort_values(ascending=False)
              .rename("VIEWS").to_string())
    print(f"\nSaved {stem}_objects / _columns / _dependencies{' / _dates' if args.scan else ''}.csv to {out_dir}")


if __name__ == "__main__":
    main()
