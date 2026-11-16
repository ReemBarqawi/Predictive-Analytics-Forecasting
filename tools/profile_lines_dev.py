"""
profile_lines_dev.py: read-only search of the warehouse for claim development history of the lines other than
motor (Fire, Liabilities, Engineering, General Accident, Marine). Motor's comes from CARCDE.CAR_RESTMT_OS_DET
(reserve history) and DTWCDE.V_PAY_DATA (payments); this looks for the same kind of tables for the other lines.
Only names, row counts and date ranges come back, never claim rows.

    .venv/Scripts/python.exe tools/profile_lines_dev.py

Connection: as pipeline/00_extract_oracle.py (config.local.toml + AMAN_ORACLE_PASSWORD or prompt).
Writes <raw_dir>/checks/lines_dev_*.csv (git-ignored):
  lines_dev_tables.csv    every table or view whose name suggests reserve / estimate history or payments, with
                          its row count and the columns that look like claim keys, dates and amounts
  lines_dev_coverage.csv  for those with a class or department column and a date column: rows, classes and the
                          first / last date per class (does it cover Fire, Marine, ...?)
"""
from __future__ import annotations

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

SYSTEM = ("SYS", "SYSTEM", "XDB", "MDSYS", "CTXSYS", "ORDSYS", "OLAPSYS", "WMSYS", "APEX_PUBLIC_USER", "DBSNMP",
          "OUTLN", "GSMADMIN_INTERNAL", "LBACSYS", "DVSYS", "AUDSYS", "OJVMSYS", "ORDDATA", "APPQOSSYS")
# Table names that suggest claim development: reserve / estimate history, outstanding, payments, claim movements
NAME_PATTERNS = ("%RESTMT%", "%OS_DET%", "%_OS%", "%OUTSTAND%", "%ESTIM%", "%RESERV%", "%PAY%", "%CLM%MOV%",
                 "%CLAIM%MOV%", "%CLM%HIST%", "%CLAIM%HIST%", "%CLM%TRAN%")


def query(conn, sql: str, params: dict | None = None) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql, params or {})
    return pd.DataFrame(cur.fetchall(), columns=[d[0].upper() for d in cur.description])


def main() -> None:
    out = (S.raw_dir or ROOT / "data" / "raw") / "checks"
    out.mkdir(parents=True, exist_ok=True)
    conn = _extract.connect()
    print(f"Connected (Oracle {conn.version}). Read-only: names, counts and date ranges only.")
    excl = ", ".join(f"'{s}'" for s in SYSTEM)
    like = " OR ".join(f"o.OBJECT_NAME LIKE '{p}'" for p in NAME_PATTERNS)
    with conn:
        objs = query(conn, f"""
            SELECT o.OWNER, o.OBJECT_NAME AS TABLE_NAME, o.OBJECT_TYPE
            FROM   ALL_OBJECTS o
            WHERE  o.OBJECT_TYPE IN ('TABLE', 'VIEW') AND o.OWNER NOT IN ({excl}) AND ({like})""")
        cols = query(conn, f"""
            SELECT c.OWNER, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE
            FROM   ALL_TAB_COLUMNS c
            WHERE  c.OWNER NOT IN ({excl})""")
        cols = cols.merge(objs[["OWNER", "TABLE_NAME"]], on=["OWNER", "TABLE_NAME"])
        rows = []
        for o in objs.itertuples():
            c = cols[(cols.OWNER == o.OWNER) & (cols.TABLE_NAME == o.TABLE_NAME)]
            try:
                n = int(query(conn, f'SELECT COUNT(*) AS N FROM "{o.OWNER}"."{o.TABLE_NAME}"')["N"].iloc[0])
            except Exception as e:                       # no access or a broken view: say so and go on
                n = None
                print(f"  {o.OWNER}.{o.TABLE_NAME}: not readable ({str(e).splitlines()[0][:80]})")
            pick = lambda pats: ", ".join(x for x in c.COLUMN_NAME if any(p in x for p in pats))
            rows.append({"OWNER": o.OWNER, "TABLE_NAME": o.TABLE_NAME, "TYPE": o.OBJECT_TYPE, "ROWS": n,
                         "CLASS_COLS": pick(("CLASS", "COB", "DEPT", "DEPARTMENT", "LOB")),
                         "CLAIM_COLS": pick(("CLM", "CLAIM")), "DATE_COLS": ", ".join(c.loc[c.DATA_TYPE.str.contains("DATE|TIMESTAMP"), "COLUMN_NAME"]),
                         "AMOUNT_COLS": pick(("EST", "AMT", "PAID", "VAL", "OS"))})
        tables = pd.DataFrame(rows).sort_values("ROWS", ascending=False)
        tables.to_csv(out / "lines_dev_tables.csv", index=False)
        print(f"  {len(tables)} candidate tables / views; {int((tables.ROWS > 0).sum())} with rows")

        cov = []
        for t in tables[(tables.ROWS > 0) & (tables.CLASS_COLS != "") & (tables.DATE_COLS != "")].itertuples():
            cls = t.CLASS_COLS.split(", ")[0]
            dt = t.DATE_COLS.split(", ")[0]
            try:
                d = query(conn, f'SELECT "{cls}" AS CLASS, COUNT(*) AS ROWS_, MIN("{dt}") AS FIRST_DATE, '
                                f'MAX("{dt}") AS LAST_DATE FROM "{t.OWNER}"."{t.TABLE_NAME}" GROUP BY "{cls}"')
            except Exception as e:
                print(f"  {t.OWNER}.{t.TABLE_NAME}: coverage not readable ({str(e).splitlines()[0][:80]})")
                continue
            cov.append(d.assign(OWNER=t.OWNER, TABLE_NAME=t.TABLE_NAME, CLASS_COLUMN=cls, DATE_COLUMN=dt))
        if cov:
            pd.concat(cov, ignore_index=True).to_csv(out / "lines_dev_coverage.csv", index=False)
            print(f"  coverage by class for {len(cov)} tables")
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
