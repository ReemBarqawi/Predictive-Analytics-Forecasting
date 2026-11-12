"""
profile_lines.py: read-only profile of every line of business in the Oracle warehouse, to decide
which lines can be priced and how. Only totals and fill rates come back, never policy or claim rows.

    .venv/Scripts/python.exe tools/profile_lines.py
    .venv/Scripts/python.exe tools/profile_lines.py --year 2024   # year used for the column check

Connection: same as pipeline/00_extract_oracle.py (config.local.toml + AMAN_ORACLE_PASSWORD or prompt).
Writes <raw_dir>/checks/line_profile_*.csv (git-ignored):
  line_profile_classes.csv   department x class: policies, premium, years covered
  line_profile_claims.csv    department: claims, paid, estimate, share that find their policy
  line_profile_columns.csv   department x V_UW_DATA column: share filled, distinct values (rating factor candidates)
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

FIRST_YEAR = 2019   # same window as sql/policies.sql

UW_KEY = ("u.VUW_OFFICE || '-' || u.VUW_DEPT_NO || '-' || u.VUW_CLASS || '-' || u.VUW_POL_TYPE"
          " || '-' || u.VUW_SOURCE || '-' || u.VUW_POL_NO")
CLM_KEY = ("c.OFFICE_NO || '-' || c.DEPARTMENT_NO || '-' || c.COB || '-' || c.POLICY_TYPE"
           " || '-' || c.SOURCE_NO || '-' || c.POLICY_NO")

CLASSES_SQL = f"""
SELECT u.VUW_DEPT_DESC AS DEPARTMENT, u.VUW_CLASS_DESC AS CLASS_DESC,
       COUNT(DISTINCT {UW_KEY} || '-' || u.VUW_POL_YEAR) AS POLICY_YEARS,
       SUM(u.VUW_TOTAL_PREM) AS TOTAL_PREMIUM,
       MIN(u.VUW_POL_YEAR) AS FIRST_YEAR, MAX(u.VUW_POL_YEAR) AS LAST_YEAR,
       MAX(u.VUW_EFFECTIVE_DT) AS LAST_EFFECTIVE
FROM   DTWCDE.V_UW_DATA u
WHERE  u.VUW_POL_YEAR >= {FIRST_YEAR}
GROUP  BY u.VUW_DEPT_DESC, u.VUW_CLASS_DESC
"""

CLAIMS_SQL = f"""
WITH pol AS (
    SELECT DISTINCT {UW_KEY} AS PKEY, u.VUW_POL_YEAR AS PYEAR
    FROM   DTWCDE.V_UW_DATA u
    WHERE  u.VUW_POL_YEAR >= {FIRST_YEAR})
SELECT c.DEPARTMENT_NAME AS DEPARTMENT,
       COUNT(*) AS CLAIMS,
       SUM(c.TOTAL_PAID) AS TOTAL_PAID,
       SUM(c.TOTAL_EST) AS TOTAL_EST,
       SUM(CASE WHEN p.PKEY IS NOT NULL THEN 1 ELSE 0 END) AS CLAIMS_MATCHED,
       MIN(c.ACC_DATE) AS FIRST_ACC, MAX(c.ACC_DATE) AS LAST_ACC
FROM   DTWCDE.V_CLM_DATA c
LEFT   JOIN pol p ON p.PKEY = {CLM_KEY} AND p.PYEAR = c.POLICY_YEAR
WHERE  c.POLICY_YEAR >= {FIRST_YEAR}
GROUP  BY c.DEPARTMENT_NAME
"""

COLUMNS_SQL = """
SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS
WHERE  OWNER = 'DTWCDE' AND TABLE_NAME = 'V_UW_DATA' ORDER BY COLUMN_ID
"""


def query(conn, sql: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql)
    return pd.DataFrame(cur.fetchall(), columns=[d[0].upper() for d in cur.description])


def column_profile(conn, year: int) -> pd.DataFrame:
    """Share filled and distinct values of every V_UW_DATA column, by department, for one policy year."""
    cols = query(conn, COLUMNS_SQL)["COLUMN_NAME"].tolist()
    parts = []
    for i in range(0, len(cols), 40):                      # keep each SELECT list a moderate size
        chunk = cols[i:i + 40]
        sel = ", ".join(f'COUNT("{c}") AS "F{j}", APPROX_COUNT_DISTINCT("{c}") AS "D{j}"'
                        for j, c in enumerate(chunk))
        df = query(conn, f"SELECT VUW_DEPT_DESC AS DEPARTMENT, COUNT(*) AS N_ROWS, {sel} "
                         f"FROM DTWCDE.V_UW_DATA WHERE VUW_POL_YEAR = {year} GROUP BY VUW_DEPT_DESC")
        for j, c in enumerate(chunk):
            parts.append(pd.DataFrame({"DEPARTMENT": df["DEPARTMENT"], "COLUMN_NAME": c,
                                       "SHARE_FILLED": df[f"F{j}"] / df["N_ROWS"],
                                       "DISTINCT_VALUES": df[f"D{j}"], "N_ROWS": df["N_ROWS"]}))
    out = pd.concat(parts, ignore_index=True)
    out["POLICY_YEAR"] = year
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--year", type=int, default=2024, help="policy year for the column check")
    args = ap.parse_args()

    out_dir = (S.raw_dir or ROOT / "data" / "raw") / "checks"
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = _extract.connect()
    print(f"Connected (Oracle {conn.version}). Read-only queries, totals only.")
    with conn:
        classes = query(conn, CLASSES_SQL).sort_values(["DEPARTMENT", "TOTAL_PREMIUM"], ascending=[True, False])
        classes.to_csv(out_dir / "line_profile_classes.csv", index=False)
        print(f"  classes: {len(classes)} department/class pairs")

        claims = query(conn, CLAIMS_SQL)
        claims["MATCH_RATE"] = claims["CLAIMS_MATCHED"] / claims["CLAIMS"]
        claims.to_csv(out_dir / "line_profile_claims.csv", index=False)
        print(f"  claims: {len(claims)} departments")

        cols = column_profile(conn, args.year)
        cols.to_csv(out_dir / "line_profile_columns.csv", index=False)
        print(f"  columns: {cols['COLUMN_NAME'].nunique()} columns x {cols['DEPARTMENT'].nunique()} departments")

    dept = (classes.groupby("DEPARTMENT")[["POLICY_YEARS", "TOTAL_PREMIUM"]].sum()
            .join(claims.set_index("DEPARTMENT")[["CLAIMS", "MATCH_RATE"]], how="outer"))
    print("\nBy department (premium in millions):")
    print(dept.assign(TOTAL_PREMIUM=(dept["TOTAL_PREMIUM"] / 1e6).round(1),
                      MATCH_RATE=dept["MATCH_RATE"].map("{:.1%}".format))
          .sort_values("TOTAL_PREMIUM", ascending=False).to_string())
    print(f"\nSaved to {out_dir}")


if __name__ == "__main__":
    main()
