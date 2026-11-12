"""
check_other_lines.py: plan F2, read-only checks before pricing the non-motor lines (FGA, Marine).
Only totals come back, never policy or claim rows.

  1. Premium basis   V_UW_DATA total / gross / net premium and sum insured by department x class x year,
                     next to the reinsurance bordereau's retained and ceded premium (FGA): is the premium
                     the pipeline reads gross, and how much of Fire does the company keep?
  2. Concentration   per class x year: policies, the largest policy's share and the top 10's share of
                     premium (a few big risks make a loss ratio swing)
  3. Claims basis    V_CLM_DATA estimate / paid / recovery by department x class x year, next to the
                     reinsurance recoveries (VW_REI_CLAIMS_RECOVERY): are claims gross of reinsurance?
  4. Unmatched       why non-motor claims miss their policy: the 6-part key in another year, the same
                     policy number and year under another office / type / source / class, only in the
                     loss-ratio view, or nowhere

    .venv/Scripts/python.exe tools/check_other_lines.py

Connection: same as pipeline/00_extract_oracle.py (config.local.toml + AMAN_ORACLE_PASSWORD or prompt).
Writes <raw_dir>/checks/other_lines_*.csv (git-ignored).
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

FIRST_YEAR = 2019            # same window as the line profile (tools/profile_lines.py)
LINES = "('FGA', 'Marine')"

UW_KEY = ("u.VUW_OFFICE || '-' || u.VUW_DEPT_NO || '-' || u.VUW_CLASS || '-' || u.VUW_POL_TYPE"
          " || '-' || u.VUW_SOURCE || '-' || u.VUW_POL_NO")
CLM_KEY = ("c.OFFICE_NO || '-' || c.DEPARTMENT_NO || '-' || c.COB || '-' || c.POLICY_TYPE"
           " || '-' || c.SOURCE_NO || '-' || c.POLICY_NO")

PREMIUM_SQL = f"""
SELECT u.VUW_DEPT_DESC AS DEPARTMENT, u.VUW_CLASS_DESC AS CLASS_DESC, u.VUW_POL_YEAR AS POLICY_YEAR,
       COUNT(DISTINCT {UW_KEY}) AS POLICIES, COUNT(*) AS ROWS_,
       SUM(u.VUW_TOTAL_PREM) AS TOTAL_PREM, SUM(u.VUW_GROSS_PREM) AS GROSS_PREM, SUM(u.VUW_NET_PREM) AS NET_PREM,
       SUM(u.VUW_SI) AS SUM_INSURED, SUM(u.VUW_AGENT_COMM_LAMT) AS AGENT_COMM
FROM   DTWCDE.V_UW_DATA u
WHERE  u.VUW_POL_YEAR >= {FIRST_YEAR} AND u.VUW_DEPT_DESC IN {LINES}
GROUP  BY u.VUW_DEPT_DESC, u.VUW_CLASS_DESC, u.VUW_POL_YEAR
"""

REINSURANCE_SQL = f"""
SELECT b.DEPT_NAME AS DEPARTMENT, b.CLASS_OF_BUSINESS AS CLASS_DESC, b.POL_YEAR AS POLICY_YEAR,
       COUNT(*) AS ROWS_, SUM(b.POLICY_PREM) AS POLICY_PREM, SUM(b.POLICY_SI) AS POLICY_SI,
       SUM(b.RETENTION_PREM) AS RETENTION_PREM, SUM(b.QS_PREM) AS QS_PREM, SUM(b.SURPLUS1_PREM) AS SURPLUS1_PREM,
       SUM(b.FAC_LOCAL_PREM) AS FAC_LOCAL_PREM, SUM(b.FAC_FOREIGN_PREM) AS FAC_FOREIGN_PREM,
       SUM(b.RETAINED_ACC_PREM) AS RETAINED_ACC_PREM
FROM   DTWCDE.VW_REI_PRODUCTION_BORDEREAUX_FGA b
WHERE  b.POL_YEAR >= {FIRST_YEAR}
GROUP  BY b.DEPT_NAME, b.CLASS_OF_BUSINESS, b.POL_YEAR
"""

CONCENTRATION_SQL = f"""
WITH pol AS (
    SELECT u.VUW_DEPT_DESC AS DEPARTMENT, u.VUW_CLASS_DESC AS CLASS_DESC, u.VUW_POL_YEAR AS POLICY_YEAR,
           {UW_KEY} AS PKEY, SUM(u.VUW_TOTAL_PREM) AS PREM
    FROM   DTWCDE.V_UW_DATA u
    WHERE  u.VUW_POL_YEAR >= {FIRST_YEAR} AND u.VUW_DEPT_DESC IN {LINES}
    GROUP  BY u.VUW_DEPT_DESC, u.VUW_CLASS_DESC, u.VUW_POL_YEAR, {UW_KEY}),
ranked AS (
    SELECT p.*, ROW_NUMBER() OVER (PARTITION BY DEPARTMENT, CLASS_DESC, POLICY_YEAR ORDER BY PREM DESC) AS RNK
    FROM   pol p)
SELECT DEPARTMENT, CLASS_DESC, POLICY_YEAR, COUNT(*) AS POLICIES, SUM(PREM) AS PREMIUM,
       MAX(CASE WHEN RNK = 1 THEN PREM END) AS LARGEST, SUM(CASE WHEN RNK <= 10 THEN PREM END) AS TOP10,
       MEDIAN(PREM) AS MEDIAN_PREMIUM, SUM(CASE WHEN PREM <= 0 THEN 1 ELSE 0 END) AS ZERO_OR_NEGATIVE
FROM   ranked
GROUP  BY DEPARTMENT, CLASS_DESC, POLICY_YEAR
"""

CLAIMS_SQL = f"""
SELECT c.DEPARTMENT_NAME AS DEPARTMENT, c.COB_DESC AS CLASS_DESC, c.POLICY_YEAR,
       COUNT(*) AS CLAIMS, SUM(c.TOTAL_EST) AS TOTAL_EST, SUM(c.TOTAL_PAID) AS TOTAL_PAID,
       SUM(c.RECOVERY_AMT) AS RECOVERY_AMT, MAX(c.TOTAL_EST) AS LARGEST_EST
FROM   DTWCDE.V_CLM_DATA c
WHERE  c.POLICY_YEAR >= {FIRST_YEAR} AND c.DEPARTMENT_NAME IN {LINES}
GROUP  BY c.DEPARTMENT_NAME, c.COB_DESC, c.POLICY_YEAR
"""

RECOVERY_SQL = f"""
SELECT r.DEPT AS DEPARTMENT, r.CLASS_OF_BUSINESS AS CLASS_DESC, r.CLAIM_YEAR,
       r.PARTICIPANT_TYPE_DESC AS PARTICIPANT, r.REQ_TYPE_D AS REQUEST,
       COUNT(DISTINCT r.CLAIM_NO) AS CLAIMS, SUM(r.VALUE_AMT) AS VALUE_AMT
FROM   DTWCDE.VW_REI_CLAIMS_RECOVERY r
WHERE  r.CLAIM_YEAR >= {FIRST_YEAR} AND r.DEPT_NO IN (
           SELECT DISTINCT DEPARTMENT_NO FROM DTWCDE.V_CLM_DATA WHERE DEPARTMENT_NAME IN {LINES})
GROUP  BY r.DEPT, r.CLASS_OF_BUSINESS, r.CLAIM_YEAR, r.PARTICIPANT_TYPE_DESC, r.REQ_TYPE_D
"""

UNMATCHED_SQL = f"""
WITH pol AS (
    SELECT DISTINCT {UW_KEY} AS PKEY, u.VUW_POL_YEAR AS PYEAR, u.VUW_POL_NO AS PNO, u.VUW_CLASS AS CLS
    FROM   DTWCDE.V_UW_DATA u
    WHERE  u.VUW_DEPT_DESC IN {LINES}),
lossr AS (
    SELECT DISTINCT l.CLM_NO, l.CLM_YEAR FROM DTWCDE.V_LOSSR_DATA l WHERE l.DEPARTMENT_NAME IN {LINES}),
clm AS (
    SELECT c.DEPARTMENT_NAME, c.COB_DESC, c.POLICY_YEAR, c.SOURCE_DESC, c.CLM_NO, c.CLM_YEAR, c.POLICY_NO, c.COB,
           c.TOTAL_EST, c.TOTAL_PAID, {CLM_KEY} AS CKEY
    FROM   DTWCDE.V_CLM_DATA c
    WHERE  c.POLICY_YEAR >= {FIRST_YEAR} AND c.DEPARTMENT_NAME IN {LINES}),
flagged AS (
    SELECT k.DEPARTMENT_NAME AS DEPARTMENT, k.COB_DESC AS CLASS_DESC, k.POLICY_YEAR, k.SOURCE_DESC,
           k.TOTAL_EST, k.TOTAL_PAID,
           CASE WHEN EXISTS (SELECT 1 FROM pol p WHERE p.PKEY = k.CKEY AND p.PYEAR = k.POLICY_YEAR) THEN '1 matched'
                WHEN EXISTS (SELECT 1 FROM pol p WHERE p.PKEY = k.CKEY) THEN '2 same key, other policy year'
                WHEN EXISTS (SELECT 1 FROM pol p WHERE p.PNO = k.POLICY_NO AND p.PYEAR = k.POLICY_YEAR AND p.CLS = k.COB)
                     THEN '3 same number, year and class; other office / type / source'
                WHEN EXISTS (SELECT 1 FROM pol p WHERE p.PNO = k.POLICY_NO AND p.PYEAR = k.POLICY_YEAR)
                     THEN '4 same number and year; other class'
                WHEN EXISTS (SELECT 1 FROM lossr l WHERE l.CLM_NO = k.CLM_NO AND l.CLM_YEAR = k.CLM_YEAR)
                     THEN '5 only in the loss-ratio view'
                ELSE '6 no policy found' END AS STATUS
    FROM   clm k)
SELECT DEPARTMENT, CLASS_DESC, POLICY_YEAR, SOURCE_DESC, STATUS,
       COUNT(*) AS CLAIMS, SUM(TOTAL_EST) AS TOTAL_EST, SUM(TOTAL_PAID) AS TOTAL_PAID
FROM   flagged
GROUP  BY DEPARTMENT, CLASS_DESC, POLICY_YEAR, SOURCE_DESC, STATUS
"""

QUERIES = {"premium": PREMIUM_SQL, "reinsurance": REINSURANCE_SQL, "concentration": CONCENTRATION_SQL,
           "claims": CLAIMS_SQL, "recovery": RECOVERY_SQL, "unmatched": UNMATCHED_SQL}


def query(conn, sql: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql)
    return pd.DataFrame(cur.fetchall(), columns=[d[0].upper().rstrip("_") for d in cur.description])


def main() -> None:
    out_dir = (S.raw_dir or ROOT / "data" / "raw") / "checks"
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = _extract.connect()
    print(f"Connected (Oracle {conn.version}). Read-only queries, totals only.")
    with conn:
        for name, sql in QUERIES.items():
            try:
                df = query(conn, sql)
            except Exception as e:                 # one failed view does not stop the others
                print(f"  {name}: FAILED - {str(e).splitlines()[0]}")
                continue
            df.to_csv(out_dir / f"other_lines_{name}.csv", index=False)
            print(f"  {name}: {len(df)} rows")
    print(f"\nSaved to {out_dir}")


if __name__ == "__main__":
    main()
