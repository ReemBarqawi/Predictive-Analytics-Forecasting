"""
profile_renewal.py: read-only search for a key that links a motor policy to its renewal, so the
optimizer's renewal rate and price sensitivity can come from data instead of config.toml.
Only table metadata, category labels and totals come back, never policy, customer or vehicle rows.

    .venv/Scripts/python.exe tools/profile_renewal.py
    .venv/Scripts/python.exe tools/profile_renewal.py --links-only             # skip the discovery steps
    .venv/Scripts/python.exe tools/profile_renewal.py --before 30 --after 60   # renewal window, days
    .venv/Scripts/python.exe tools/profile_renewal.py --lines                  # fire, general, marine, medical

Connection: same as pipeline/00_extract_oracle.py (config.local.toml + AMAN_ORACLE_PASSWORD or prompt).
Writes <raw_dir>/checks/ (git-ignored):
  Discovery (skipped with --links-only)
  renewal_candidate_columns.csv  every column, in any schema you can read, whose name suggests a link
                                 (previous / renewal / original policy, chassis, plate, VIN, ...)
  renewal_vehicle_columns.csv    all columns of CARCDE.VCAR_MAX_VEHICLE_ENDT and of tables named like
                                 PREV / RENEW, with fill share and distinct values
  renewal_labels.csv             motor source / policy type labels with policy counts
  Link tests (one pass each with window functions, so minutes rather than hours)
  renewal_ins_endt_columns.csv   columns of CARCDE.CAR_INS_ENDT (statistics, or a 1% sample)
  renewal_ins_endt_flags.csv     by policy year: rows, ECAR_PREV_REN_ID filled, and the counts of the
                                 ECAR_COMPANY_RENEWAL_FLAG and ORIGINAL_FLAG values
  renewal_vehicle_test.csv       by expiry year: expiring vehicles (chassis number) and the share insured
                                 again inside the window; of those, how many kept the policy number or
                                 changed source (direct / broker)
  renewal_link_test.csv          by expiry year: expiring motor policies (V_UW_DATA) and the share whose
                                 next policy starts inside the window, by the same policy number and by
                                 the same customer name and class
  Other lines (--lines only)
  renewal_lines_columns.csv      key, date and link columns picked in each line's table
  renewal_lines_years.csv        policies by policy year and department (shows where a line moved tables)
  renewal_lines_fill.csv         by policy year: share filled of PREV_REN_ID, original policy no, customer ID
  renewal_lines_flags.csv        by policy year: counts of each renewal flag value
  renewal_lines_test.csv         by expiry year: renewal by customer ID + class inside the window, and how
                                 many policies are later named as another policy's original
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
NAME_HINTS = ["PREV", "RENEW", "RNW", "ORIG", "OLD_POL", "PARENT", "CHASSIS", "VIN", "PLATE",
              "SEQ_NO", "SEQUENCE", "REG_NO", "IQAMA", "NATIONAL_ID", "ID_NO", "CUST_ID"]

CANDIDATES_SQL = """
SELECT c.OWNER, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, t.NUM_ROWS, t.LAST_ANALYZED
FROM   ALL_TAB_COLUMNS c
LEFT   JOIN ALL_TABLES t ON t.OWNER = c.OWNER AND t.TABLE_NAME = c.TABLE_NAME
WHERE  c.OWNER NOT IN ('SYS', 'SYSTEM', 'XDB', 'MDSYS', 'CTXSYS', 'ORDSYS', 'WMSYS', 'OLAPSYS',
                       'DVSYS', 'LBACSYS', 'GSMADMIN_INTERNAL', 'APEX_PUBLIC_USER')
  AND  c.OWNER NOT LIKE 'APEX%'
  AND  ({hints})
ORDER  BY c.OWNER, c.TABLE_NAME, c.COLUMN_ID
"""

TABLES_SQL = """
SELECT OWNER, TABLE_NAME FROM ALL_TAB_COLUMNS
WHERE  (OWNER = 'CARCDE' AND TABLE_NAME = 'VCAR_MAX_VEHICLE_ENDT')
   OR  (OWNER IN ('DTWCDE', 'CARCDE', 'GENCDE') AND (TABLE_NAME LIKE '%PREV%' OR TABLE_NAME LIKE '%RENEW%'))
GROUP  BY OWNER, TABLE_NAME
"""

LABELS_SQL = """
SELECT u.VUW_POL_YEAR AS POLICY_YEAR, u.VUW_SOURCE_DESC AS SOURCE_DESC, u.VUW_POL_TYPE_DESC AS POLICY_TYPE_DESC,
       COUNT(DISTINCT u.VUW_OFFICE || '-' || u.VUW_CLASS || '-' || u.VUW_POL_TYPE || '-' || u.VUW_SOURCE
             || '-' || u.VUW_POL_NO) AS POLICIES
FROM   DTWCDE.V_UW_DATA u
WHERE  u.VUW_DEPT_DESC = 'Motor' AND u.VUW_POL_YEAR >= {first}
GROUP  BY u.VUW_POL_YEAR, u.VUW_SOURCE_DESC, u.VUW_POL_TYPE_DESC
"""

LINK_SQL = """
WITH p AS (
    SELECT u.VUW_OFFICE || '-' || u.VUW_DEPT_NO || '-' || u.VUW_CLASS || '-' || u.VUW_POL_TYPE
               || '-' || u.VUW_SOURCE || '-' || u.VUW_POL_NO AS PKEY,
           u.VUW_POL_YEAR AS PY,
           MAX(UPPER(TRIM(u.VUW_CUST_NAME))) AS CUST,
           MAX(u.VUW_CLASS) AS CLS,
           MIN(u.VUW_EFFECTIVE_DT) AS EFF,
           MAX(u.VUW_EXP_DT) AS EXP
    FROM   DTWCDE.V_UW_DATA u
    WHERE  u.VUW_DEPT_DESC = 'Motor' AND u.VUW_POL_YEAR >= {first}
    GROUP  BY u.VUW_OFFICE, u.VUW_DEPT_NO, u.VUW_CLASS, u.VUW_POL_TYPE, u.VUW_SOURCE,
              u.VUW_POL_NO, u.VUW_POL_YEAR
    HAVING SUM(u.VUW_TOTAL_PREM) > 0),
q AS (
    SELECT p.*,
           LEAD(EFF) OVER (PARTITION BY PKEY ORDER BY EFF, PY) AS NEXT_SAME_NO,
           LEAD(EFF) OVER (PARTITION BY CUST, CLS ORDER BY EFF, PY) AS NEXT_SAME_CUST
    FROM   p)
SELECT EXTRACT(YEAR FROM EXP) AS EXPIRY_YEAR,
       COUNT(*) AS EXPIRING,
       SUM(CASE WHEN NEXT_SAME_NO BETWEEN EXP - {before} AND EXP + {after} THEN 1 ELSE 0 END) AS SAME_POLICY_NO,
       SUM(CASE WHEN NEXT_SAME_CUST BETWEEN EXP - {before} AND EXP + {after} THEN 1 ELSE 0 END) AS SAME_CUSTOMER
FROM   q
WHERE  EXP >= DATE '{first_exp}-01-01' AND CUST IS NOT NULL
GROUP  BY EXTRACT(YEAR FROM EXP)
ORDER  BY 1
"""

VEHICLE_SQL = """
WITH v AS (
    SELECT UPPER(TRIM(VVCL_CHASSIS_NO)) AS CH,
           VVCL_POL_OFFICE || '-' || VVCL_POLICY_TYPE || '-' || VVCL_POL_NO AS PNO,
           VVCL_POL_YEAR AS PY,
           MAX(VVCL_SOURCE) AS SRC,
           MIN(VVCL_INS_ST_DT) AS ST,
           MAX(VVCL_INS_ED_DT) AS ED
    FROM   CARCDE.VCAR_MAX_VEHICLE_ENDT
    WHERE  VVCL_POL_YEAR >= {first} AND LENGTH(TRIM(VVCL_CHASSIS_NO)) >= 8
    GROUP  BY UPPER(TRIM(VVCL_CHASSIS_NO)), VVCL_POL_OFFICE, VVCL_POLICY_TYPE, VVCL_POL_NO, VVCL_POL_YEAR),
w AS (
    SELECT v.*,
           LEAD(ST)  OVER (PARTITION BY CH ORDER BY ST, PY) AS NST,
           LEAD(PNO) OVER (PARTITION BY CH ORDER BY ST, PY) AS NPNO,
           LEAD(SRC) OVER (PARTITION BY CH ORDER BY ST, PY) AS NSRC
    FROM   v),
f AS (
    SELECT w.*, CASE WHEN NST BETWEEN ED - {before} AND ED + {after} THEN 1 ELSE 0 END AS RENEWED
    FROM   w WHERE ED IS NOT NULL)
SELECT EXTRACT(YEAR FROM ED) AS EXPIRY_YEAR,
       COUNT(*) AS EXPIRING_VEHICLES,
       COUNT(DISTINCT CH) AS DISTINCT_CHASSIS,
       SUM(RENEWED) AS RENEWED,
       SUM(CASE WHEN RENEWED = 1 AND NPNO = PNO THEN 1 ELSE 0 END) AS RENEWED_SAME_POLICY_NO,
       SUM(CASE WHEN RENEWED = 1 AND NSRC <> SRC THEN 1 ELSE 0 END) AS RENEWED_SOURCE_CHANGED,
       SUM(CASE WHEN NST > ED + {after} THEN 1 ELSE 0 END) AS RETURNED_LATER
FROM   f
WHERE  ED >= DATE '{first_exp}-01-01'
GROUP  BY EXTRACT(YEAR FROM ED)
ORDER  BY 1
"""

GAPS_SQL = """
WITH v AS (
    SELECT UPPER(TRIM(VVCL_CHASSIS_NO)) AS CH,
           VVCL_POL_OFFICE || '-' || VVCL_POLICY_TYPE || '-' || VVCL_POL_NO AS PNO,
           VVCL_POL_YEAR AS PY,
           MAX(VVCL_POLICY_TYPE) AS PTYPE,
           MAX(VVCL_SOURCE) AS SRC,
           MAX(VVCL_STATUS) AS STATUS,
           MAX(VVCL_STATUS_DT) AS STATUS_DT,
           MIN(VVCL_INS_ST_DT) AS ST,
           MAX(VVCL_INS_ED_DT) AS ED
    FROM   CARCDE.VCAR_MAX_VEHICLE_ENDT
    WHERE  VVCL_POL_YEAR >= {first} AND LENGTH(TRIM(VVCL_CHASSIS_NO)) >= 8
    GROUP  BY UPPER(TRIM(VVCL_CHASSIS_NO)), VVCL_POL_OFFICE, VVCL_POLICY_TYPE, VVCL_POL_NO, VVCL_POL_YEAR),
w AS (
    SELECT v.*,
           LEAD(ST)  OVER (PARTITION BY CH ORDER BY ST, PY) AS NST,
           LEAD(SRC) OVER (PARTITION BY CH ORDER BY ST, PY) AS NSRC,
           -- ended early: a status date well inside the cover period (cancellation or similar)
           CASE WHEN STATUS_DT > ST AND STATUS_DT < ED - 30 THEN STATUS_DT ELSE ED END AS END_ADJ
    FROM   v WHERE ED IS NOT NULL),
g AS (
    SELECT w.*, NST - ED AS GAP, NST - END_ADJ AS GAP_ADJ FROM w)
SELECT EXTRACT(YEAR FROM ED) AS EXPIRY_YEAR, PTYPE AS POLICY_TYPE, SRC AS SOURCE, STATUS,
       COUNT(*) AS VEHICLES,
       SUM(CASE WHEN END_ADJ < ED THEN 1 ELSE 0 END) AS ENDED_EARLY,
       SUM(CASE WHEN NST IS NULL THEN 1 ELSE 0 END)                       AS NEVER_AGAIN,
       SUM(CASE WHEN GAP < -180 THEN 1 ELSE 0 END)                        AS NEXT_BEFORE_180,
       SUM(CASE WHEN GAP BETWEEN -180 AND -31 THEN 1 ELSE 0 END)          AS NEXT_31_180_BEFORE,
       SUM(CASE WHEN GAP BETWEEN -30 AND 60 THEN 1 ELSE 0 END)            AS NEXT_IN_WINDOW,
       SUM(CASE WHEN GAP BETWEEN 61 AND 365 THEN 1 ELSE 0 END)            AS NEXT_61_365_AFTER,
       SUM(CASE WHEN GAP > 365 THEN 1 ELSE 0 END)                         AS NEXT_OVER_365_AFTER,
       SUM(CASE WHEN GAP_ADJ BETWEEN -30 AND 60 THEN 1 ELSE 0 END)        AS ADJ_NEXT_IN_WINDOW,
       SUM(CASE WHEN GAP BETWEEN -30 AND 60 AND NSRC <> SRC THEN 1 ELSE 0 END) AS IN_WINDOW_SOURCE_CHANGED
FROM   g
WHERE  ED >= DATE '{first_exp}-01-01' AND ED < DATE '2025-01-01'
GROUP  BY EXTRACT(YEAR FROM ED), PTYPE, SRC, STATUS
ORDER  BY 1, 2, 3, 4
"""

COVERAGE_SQL = """
SELECT N_YEARS, COUNT(*) AS CHASSIS
FROM  (SELECT UPPER(TRIM(VVCL_CHASSIS_NO)) AS CH, COUNT(DISTINCT VVCL_POL_YEAR) AS N_YEARS
       FROM   CARCDE.VCAR_MAX_VEHICLE_ENDT
       WHERE  VVCL_POL_YEAR >= {first} AND LENGTH(TRIM(VVCL_CHASSIS_NO)) >= 8
       GROUP  BY UPPER(TRIM(VVCL_CHASSIS_NO)))
GROUP  BY N_YEARS ORDER BY 1
"""

# Price sensitivity. One row per vehicle-policy, annual premium; RENEWED within the window; for renewers the
# next policy's annual premium. Only full-year policies (330-400 days) that were not ended early (STATUS 1).
VEHICLE_POLICIES = """
v AS (
    SELECT UPPER(TRIM(VVCL_CHASSIS_NO)) AS CH,
           VVCL_POL_YEAR AS PY,
           MAX(VVCL_POL_OFFICE) AS OFFICE,
           MAX(VVCL_POLICY_TYPE) AS PTYPE,
           MAX(VVCL_CLASS_OF_BUSINESS) AS CLS,
           MAX(VVCL_SOURCE) AS SRC,
           MAX(VVCL_YEAR_OF_MAKE) AS YOM,
           MAX(VVCL_STATUS) AS STATUS,
           MIN(VVCL_INS_ST_DT) AS ST,
           MAX(VVCL_INS_ED_DT) AS ED,
           SUM(VVCL_GROSS_PREM) AS PREM
    FROM   CARCDE.VCAR_MAX_VEHICLE_ENDT
    WHERE  VVCL_POL_YEAR >= {first} AND LENGTH(TRIM(VVCL_CHASSIS_NO)) >= 8
    GROUP  BY UPPER(TRIM(VVCL_CHASSIS_NO)), VVCL_POL_OFFICE, VVCL_DEPT_NO, VVCL_CLASS_OF_BUSINESS,
              VVCL_POLICY_TYPE, VVCL_SOURCE, VVCL_POL_NO, VVCL_POL_YEAR)"""

PRICE_SQL = """
WITH {vehicle_policies},
w AS (
    SELECT v.*,
           LEAD(ST)   OVER (PARTITION BY CH ORDER BY ST, PY) AS NST,
           LEAD(ED)   OVER (PARTITION BY CH ORDER BY ST, PY) AS NED,
           LEAD(PREM) OVER (PARTITION BY CH ORDER BY ST, PY) AS NPREM
    FROM   v),
f AS (
    SELECT w.*,
           CASE WHEN NST BETWEEN ED - {before} AND ED + {after} THEN 1 ELSE 0 END AS RENEWED,
           PREM * 365 / (ED - ST) AS ANNUAL,
           CASE WHEN NED - NST BETWEEN 330 AND 400 AND NPREM > 0 THEN NPREM * 365 / (NED - NST) END AS NANNUAL,
           EXTRACT(YEAR FROM ED) - YOM AS VEH_AGE
    FROM   w
    WHERE  ED IS NOT NULL AND ED - ST BETWEEN 330 AND 400 AND STATUS = 1 AND PREM > 0),
b AS (
    SELECT f.*,
           CASE WHEN RENEWED = 0 THEN 'NOT_RENEWED'
                WHEN NANNUAL IS NULL THEN 'RENEWED_NO_PRICE'
                WHEN NANNUAL / ANNUAL - 1 < -0.20 THEN 'A <-20%'
                WHEN NANNUAL / ANNUAL - 1 < -0.10 THEN 'B -20..-10%'
                WHEN NANNUAL / ANNUAL - 1 < -0.03 THEN 'C -10..-3%'
                WHEN NANNUAL / ANNUAL - 1 <= 0.03 THEN 'D -3..+3%'
                WHEN NANNUAL / ANNUAL - 1 <= 0.10 THEN 'E +3..+10%'
                WHEN NANNUAL / ANNUAL - 1 <= 0.20 THEN 'F +10..+20%'
                WHEN NANNUAL / ANNUAL - 1 <= 0.40 THEN 'G +20..+40%'
                ELSE 'H >+40%' END AS CHANGE_BAND,
           CASE WHEN VEH_AGE IS NULL THEN 'unknown' WHEN VEH_AGE <= 2 THEN '0-2' WHEN VEH_AGE <= 5 THEN '3-5'
                WHEN VEH_AGE <= 10 THEN '6-10' ELSE '11+' END AS AGE_BAND
    FROM   f)
SELECT EXTRACT(YEAR FROM ED) AS EXPIRY_YEAR, OFFICE, PTYPE AS POLICY_TYPE, CLS AS CLASS, SRC AS SOURCE,
       AGE_BAND, CHANGE_BAND,
       COUNT(*) AS VEHICLES,
       SUM(ANNUAL) AS OLD_ANNUAL_PREM,
       SUM(CASE WHEN CHANGE_BAND NOT IN ('NOT_RENEWED', 'RENEWED_NO_PRICE') THEN NANNUAL END) AS NEW_ANNUAL_PREM
FROM   b
WHERE  ED >= DATE '{first_exp}-01-01' AND ED < DATE '2025-01-01'
GROUP  BY EXTRACT(YEAR FROM ED), OFFICE, PTYPE, CLS, SRC, AGE_BAND, CHANGE_BAND
"""

# Renewal notices: the premium offered at renewal, known for customers who left too (fleet / corporate).
NOTICE_SQL = """
WITH {vehicle_policies},
n AS (
    SELECT ROWNUM AS NID, UPPER(TRIM(CRN_CHASSIS_NO)) AS CH, CRN_INS_ST_DT AS OST, CRN_INS_ED_DT AS OED,
           CRN_PREM AS OFFER, CRN_CLASS_OF_BUSINESS AS CLS, CRN_POLICY_TYPE AS PTYPE
    FROM   CARCDE.CAR_RENEWAL_NOTICE
    WHERE  LENGTH(TRIM(CRN_CHASSIS_NO)) >= 8 AND CRN_INS_ST_DT >= DATE '{first}-01-01'
      AND  CRN_PREM > 0 AND CRN_INS_ED_DT - CRN_INS_ST_DT BETWEEN 330 AND 400),
j AS (
    SELECT n.NID, MAX(n.OST) AS OST, MAX(n.CLS) AS CLS, MAX(n.PTYPE) AS PTYPE,
           MAX(n.OFFER * 365 / (n.OED - n.OST)) AS OFFER_ANNUAL,
           MAX(CASE WHEN v.ED BETWEEN n.OST - 30 AND n.OST + 30 AND v.ED - v.ST BETWEEN 330 AND 400 AND v.PREM > 0
                    THEN v.PREM * 365 / (v.ED - v.ST) END) AS PRIOR_ANNUAL,
           MAX(CASE WHEN v.ST BETWEEN n.OST - {before} AND n.OST + {after} THEN 1 ELSE 0 END) AS RENEWED
    FROM   n LEFT JOIN v ON v.CH = n.CH
    GROUP  BY n.NID),
b AS (
    SELECT j.*,
           CASE WHEN PRIOR_ANNUAL IS NULL THEN 'NO_PRIOR'
                WHEN OFFER_ANNUAL / PRIOR_ANNUAL - 1 < -0.10 THEN 'A <-10%'
                WHEN OFFER_ANNUAL / PRIOR_ANNUAL - 1 < -0.03 THEN 'B -10..-3%'
                WHEN OFFER_ANNUAL / PRIOR_ANNUAL - 1 <= 0.03 THEN 'C -3..+3%'
                WHEN OFFER_ANNUAL / PRIOR_ANNUAL - 1 <= 0.10 THEN 'D +3..+10%'
                WHEN OFFER_ANNUAL / PRIOR_ANNUAL - 1 <= 0.20 THEN 'E +10..+20%'
                WHEN OFFER_ANNUAL / PRIOR_ANNUAL - 1 <= 0.40 THEN 'F +20..+40%'
                ELSE 'G >+40%' END AS OFFER_BAND
    FROM   j)
SELECT EXTRACT(YEAR FROM OST) AS OFFER_YEAR, CLS AS CLASS, PTYPE AS POLICY_TYPE, OFFER_BAND,
       COUNT(*) AS NOTICES, SUM(RENEWED) AS RENEWED,
       SUM(PRIOR_ANNUAL) AS PRIOR_ANNUAL_PREM, SUM(OFFER_ANNUAL) AS OFFER_ANNUAL_PREM
FROM   b
GROUP  BY EXTRACT(YEAR FROM OST), CLS, PTYPE, OFFER_BAND
ORDER  BY 1, 2, 3, 4
"""

CODES_UW_SQL = """
SELECT '{kind}' AS KIND, TO_CHAR({code}) AS CODE, MAX({name}) AS UW_NAME,
       COUNT(DISTINCT u.VUW_OFFICE || '-' || u.VUW_CLASS || '-' || u.VUW_POL_TYPE || '-' || u.VUW_SOURCE
             || '-' || u.VUW_POL_NO) AS UW_POLICIES
FROM   DTWCDE.V_UW_DATA u
WHERE  u.VUW_DEPT_DESC = 'Motor' AND u.VUW_POL_YEAR = {year}
GROUP  BY {code}
"""

CODES_VEH_SQL = """
SELECT TO_CHAR(VVCL_POL_OFFICE) AS POL_OFFICE, TO_CHAR(VVCL_OFFICE) AS OFFICE,
       TO_CHAR(VVCL_CLASS_OF_BUSINESS) AS CLASS, TO_CHAR(VVCL_DEPT_NO) AS DEPT,
       COUNT(DISTINCT VVCL_POL_OFFICE || '-' || VVCL_CLASS_OF_BUSINESS || '-' || VVCL_POLICY_TYPE || '-'
             || VVCL_SOURCE || '-' || VVCL_POL_NO) AS VEH_POLICIES,
       COUNT(*) AS VEH_ROWS
FROM   CARCDE.VCAR_MAX_VEHICLE_ENDT
WHERE  VVCL_POL_YEAR = {year}
GROUP  BY VVCL_POL_OFFICE, VVCL_OFFICE, VVCL_CLASS_OF_BUSINESS, VVCL_DEPT_NO
"""

FLAGS_SQL = """
SELECT "{year_col}" AS POLICY_YEAR, ECAR_COMPANY_RENEWAL_FLAG AS RENEWAL_FLAG, ORIGINAL_FLAG,
       COUNT(*) AS N_ROWS, COUNT(ECAR_PREV_REN_ID) AS PREV_REN_ID_FILLED,
       APPROX_COUNT_DISTINCT(ECAR_PREV_REN_ID) AS PREV_REN_ID_DISTINCT
FROM   CARCDE.CAR_INS_ENDT
WHERE  "{year_col}" >= {first}
GROUP  BY "{year_col}", ECAR_COMPANY_RENEWAL_FLAG, ORIGINAL_FLAG
ORDER  BY 1, 2, 3
"""


def query(conn, sql: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql)
    return pd.DataFrame(cur.fetchall(), columns=[d[0].upper() for d in cur.description])


def table_profile(conn, owner: str, table: str) -> pd.DataFrame:
    """Fill share and distinct values of each column, read from optimizer statistics when present
    (no scan), otherwise counted on a 1% sample."""
    cols = query(conn, f"SELECT c.COLUMN_NAME, c.DATA_TYPE, c.NUM_DISTINCT, c.NUM_NULLS, t.NUM_ROWS "
                       f"FROM ALL_TAB_COLUMNS c LEFT JOIN ALL_TABLES t "
                       f"ON t.OWNER = c.OWNER AND t.TABLE_NAME = c.TABLE_NAME "
                       f"WHERE c.OWNER = '{owner}' AND c.TABLE_NAME = '{table}' ORDER BY c.COLUMN_ID")
    if cols["NUM_ROWS"].notna().any() and cols["NUM_DISTINCT"].notna().any():
        cols["SHARE_FILLED"] = 1 - cols["NUM_NULLS"] / cols["NUM_ROWS"]
        cols["DISTINCT_VALUES"] = cols["NUM_DISTINCT"]
        cols["BASIS"] = "statistics"
    else:
        names = cols["COLUMN_NAME"].tolist()
        sel = ", ".join(f'COUNT("{c}") AS "F{j}", APPROX_COUNT_DISTINCT("{c}") AS "D{j}"'
                        for j, c in enumerate(names))
        basis = "1% sample"
        try:
            r = query(conn, f'SELECT COUNT(*) AS N, {sel} FROM "{owner}"."{table}" SAMPLE (1)').iloc[0]
        except Exception as e:  # a view cannot be sampled: count it whole
            print(f"    {table}: sample not allowed ({type(e).__name__}), counting all rows")
            r = query(conn, f'SELECT COUNT(*) AS N, {sel} FROM "{owner}"."{table}"').iloc[0]
            basis = "all rows"
        cols["SHARE_FILLED"] = [r[f"F{j}"] / max(r["N"], 1) for j in range(len(names))]
        cols["DISTINCT_VALUES"] = [r[f"D{j}"] for j in range(len(names))]
        cols["NUM_ROWS"] = r["N"]
        cols["BASIS"] = basis
    cols.insert(0, "TABLE_NAME", table)
    cols.insert(0, "OWNER", owner)
    return cols[["OWNER", "TABLE_NAME", "COLUMN_NAME", "DATA_TYPE", "NUM_ROWS",
                 "SHARE_FILLED", "DISTINCT_VALUES", "BASIS"]]


def discovery(conn, out_dir: Path) -> None:
    hints = " OR ".join(f"c.COLUMN_NAME LIKE '%{h}%'" for h in NAME_HINTS)
    cand = query(conn, CANDIDATES_SQL.format(hints=hints))
    cand.to_csv(out_dir / "renewal_candidate_columns.csv", index=False)
    print(f"  candidate columns: {len(cand)} in {cand[['OWNER', 'TABLE_NAME']].drop_duplicates().shape[0]} tables")

    tables = query(conn, TABLES_SQL)
    prof = [table_profile(conn, o, t) for o, t in tables.itertuples(index=False)]
    prof = pd.concat(prof, ignore_index=True) if prof else pd.DataFrame()
    prof.to_csv(out_dir / "renewal_vehicle_columns.csv", index=False)
    print(f"  tables profiled: {', '.join(tables['TABLE_NAME']) or 'none found'}")

    labels = query(conn, LABELS_SQL.format(first=FIRST_YEAR))
    labels.to_csv(out_dir / "renewal_labels.csv", index=False)
    print(f"  source / policy type labels: {len(labels)} rows")


GAP_BUCKETS = ["NEXT_BEFORE_180", "NEXT_31_180_BEFORE", "NEXT_IN_WINDOW", "NEXT_61_365_AFTER",
               "NEXT_OVER_365_AFTER", "NEVER_AGAIN"]


def run_gaps(conn, out_dir: Path, window: dict) -> None:
    """Where each expiring vehicle goes next. Writes renewal_gaps.csv (by expiry year, policy type,
    source and status) and renewal_coverage.csv (how many policy years each chassis appears in)."""
    print("  1/2 gaps to the next policy (chassis number): running, about as long as the vehicle test...")
    gaps = query(conn, GAPS_SQL.format(**window))
    gaps.to_csv(out_dir / "renewal_gaps.csv", index=False)
    print("  2/2 policy years per chassis...")
    cov = query(conn, COVERAGE_SQL.format(**window))
    cov.to_csv(out_dir / "renewal_coverage.csv", index=False)

    by_year = gaps.groupby("EXPIRY_YEAR")[["VEHICLES", "ENDED_EARLY", "ADJ_NEXT_IN_WINDOW"] + GAP_BUCKETS].sum()
    share = by_year[GAP_BUCKETS + ["ADJ_NEXT_IN_WINDOW", "ENDED_EARLY"]].div(by_year["VEHICLES"], axis=0)
    print("\nWhere expiring vehicles go next (share of vehicles; ADJ = using the early end date when there is one):")
    print(pd.concat([by_year["VEHICLES"], share], axis=1)
          .to_string(formatters={c: "{:.1%}".format for c in share.columns}))
    print("\nPolicy years each chassis appears in:")
    print(cov.to_string(index=False))
    print(f"\nSaved renewal_gaps.csv and renewal_coverage.csv to {out_dir}")


def run_price(conn, out_dir: Path, window: dict, notices: bool) -> None:
    """Renewal against price change. Writes renewal_price.csv (by expiry year x office x policy type x class
    x source x vehicle age x price-change band) and, with notices, renewal_notice_price.csv. Fit them with
    tools/fit_price_sensitivity.py (no database needed)."""
    vp = VEHICLE_POLICIES.format(**window)
    print("  1/{n} renewal by price change (chassis link): running, about as long as the gap check...".format(
        n=2 if notices else 1))
    price = query(conn, PRICE_SQL.format(vehicle_policies=vp, **window))
    price.to_csv(out_dir / "renewal_price.csv", index=False)
    t = price.groupby("CHANGE_BAND")["VEHICLES"].sum()
    print(f"      saved: {int(t.sum()):,} vehicles; renewed with a price: "
          f"{int(t.drop(['NOT_RENEWED', 'RENEWED_NO_PRICE'], errors='ignore').sum()):,}")
    if notices:
        print("  2/2 renewal notices (offered premium)...")
        nt = query(conn, NOTICE_SQL.format(vehicle_policies=vp, **window))
        nt.to_csv(out_dir / "renewal_notice_price.csv", index=False)
        print(f"      saved: {int(nt['NOTICES'].sum()):,} notices")
    print(f"\nSaved to {out_dir}. Next: .venv/Scripts/python.exe tools/fit_price_sensitivity.py")


def run_codes(conn, out_dir: Path, year: int) -> None:
    """Office and class codes of V_UW_DATA (motor) and VCAR_MAX_VEHICLE_ENDT side by side for one policy
    year, with policy counts, to see whether the two tables use the same codes. Writes renewal_codes.csv."""
    print(f"  1/2 V_UW_DATA codes, policy year {year}...")
    uw = pd.concat([query(conn, CODES_UW_SQL.format(kind="OFFICE", code="u.VUW_OFFICE", name="u.OFFICE_NAME", year=year)),
                    query(conn, CODES_UW_SQL.format(kind="CLASS", code="u.VUW_CLASS", name="u.VUW_CLASS_DESC", year=year))])
    print(f"  2/2 VCAR_MAX_VEHICLE_ENDT codes, policy year {year} (the view is slow)...")
    veh = query(conn, CODES_VEH_SQL.format(year=year))
    veh.to_csv(out_dir / "renewal_codes_vehicle_detail.csv", index=False)

    sides = []
    for kind, col in (("OFFICE", "POL_OFFICE"), ("OFFICE (VVCL_OFFICE)", "OFFICE"), ("CLASS", "CLASS")):
        v = veh.groupby(col)[["VEH_POLICIES", "VEH_ROWS"]].sum().rename_axis("CODE").reset_index()
        u = uw[uw["KIND"] == kind.split()[0]][["CODE", "UW_NAME", "UW_POLICIES"]]
        m = u.merge(v, on="CODE", how="outer")
        m.insert(0, "KIND", kind)
        sides.append(m)
    out = pd.concat(sides, ignore_index=True)
    out["CODE_NUM"] = pd.to_numeric(out["CODE"], errors="coerce")
    out = out.sort_values(["KIND", "CODE_NUM", "CODE"]).drop(columns="CODE_NUM")
    out.to_csv(out_dir / "renewal_codes.csv", index=False)

    pd.set_option("display.width", 200)
    for kind, g in out.groupby("KIND", sort=False):
        both = g["UW_POLICIES"].notna() & g["VEH_POLICIES"].notna()
        print(f"\n{kind}: {both.sum()} codes in both, {g['UW_POLICIES'].notna().sum()} in V_UW_DATA, "
              f"{g['VEH_POLICIES'].notna().sum()} in the vehicle table")
        print(g.drop(columns="KIND").to_string(index=False, na_rep="-"))
    print(f"\nSaved renewal_codes.csv and renewal_codes_vehicle_detail.csv to {out_dir}")


# --lines: renewal fields of the non-motor lines. The link fields were named in the discovery run
# (renewal_candidate_columns.csv); the key and date columns are picked from each table's own column list
# at run time and printed, so a wrong pick is visible.
LINES = [
    dict(line="Fire", owner="FIRCDE", table="FIR_INS_ENDT", prefix="EFIR_",
         prev="EFIR_PREV_REN_ID", orig="EFIR_ORIGINAL_POL_NO", flag="EFIR_COMPANY_RENEWAL_FLAG"),
    dict(line="Fire (backup 2024-04-02)", owner="FIRCDE", table="FIR_INS_ENDT_02_04_2024", prefix="EFIR_",
         prev="EFIR_PREV_REN_ID", orig="EFIR_ORIGINAL_POL_NO", flag="EFIR_COMPANY_RENEWAL_FLAG"),
    dict(line="General", owner="GENCDE", table="GEN_UW_POL_MASTERS", prefix="GUP_",
         prev="GUP_PREV_REN_ID", orig="GUP_ORIGINAL_POL_NO", orig_year="GUP_ORIGINAL_POL_YEAR",
         flag="GUP_COMPANY_RENEWAL_FLAG"),
    dict(line="Marine cargo", owner="MRNCDE", table="MRN_CARGO", prefix="EMRN_",
         prev="EMRN_PREV_REN_ID", orig="EMRN_ORIGINAL_POL_NO", flag="EMRN_COMPANY_RENEWAL_FLAG"),
    dict(line="Medical", owner="MEDCDE", table="MED_UW_POLICIES", prefix="UPY_",
         orig="UPY_ORIGIN_PLOICY_NO", orig_year="UPY_ORIGIN_POLICY_YEAR", flag="UPY_RENEWAL_FLAG"),
]
# suffix after the table prefix; exact names first, then looser patterns
PICK = {
    "pol_no":  [r"POL_NO", r"POLICY_NO"],
    "year":    [r"POL_YEAR", r"POLICY_YEAR", r"(?!ORIG).*POL.*YEAR"],
    "office":  [r"POL_OFFICE", r"OFFICE", r"OFFICE_NO", r"BRANCH"],
    "dept":    [r"DEPT_NO", r"DEPT", r".*DEPT.*"],
    "class":   [r"CLASS_OF_BUSINESS", r"CLASS", r"CLASS_NO", r".*CLASS.*"],
    "ptype":   [r"POLICY_TYPE", r"POL_TYPE"],
    "source":  [r"SOURCE", r"SOURCE_NO"],
    "endt":    [r"ENDT_NO", r"ENDT_SEQ"],
    "start":   [r"INS_ST_DT", r"INS_START_DT", r"ST_DT", r"START_DT", r"START_DATE", r"EFF_DT",
                r"EFFECTIVE_DATE", r"FROM_DT", r"FROM_DATE", r"INCEPTION_DT",
                r"(?!.*(IQAMA|ISSUE|ENDT)).*(ST_DT|START|EFF|FROM).*"],
    "end":     [r"INS_ED_DT", r"INS_END_DT", r"ED_DT", r"END_DT", r"END_DATE", r"EXP_DT", r"EXPIRY_DATE",
                r"EXPIRY_DT", r"TO_DT", r"TO_DATE",
                r"(?!.*(IQAMA|ISSUE|ENDT)).*(ED_DT|END_D|EXP|TO_D).*"],
    "cust":    [r"INSURED_IQAMA_NO", r"CUST_ID_NO", r"CUST_NO", r"CUST_CODE", r"CUSTOMER_NO", r"INSURED_NO",
                r"INSURED_CODE", r"(?!.*TYPE).*CUST.*"],
}
DATE_TYPES = ("DATE", "TIMESTAMP")


def pick_columns(cols: pd.DataFrame, prefix: str) -> dict:
    import re
    own = cols[cols["COLUMN_NAME"].str.startswith(prefix)]
    out = {}
    for role, patterns in PICK.items():
        pool = own
        if role in ("start", "end"):
            pool = own[own["DATA_TYPE"].str.startswith(DATE_TYPES)]
        rest = {c[len(prefix):]: c for c in pool["COLUMN_NAME"]}
        for pat in patterns:
            hit = next((full for suf, full in rest.items() if re.fullmatch(pat, suf)), None)
            if hit:
                out[role] = hit
                break
    return out


def lines_sql(cfg: dict, k: dict, window: dict) -> dict:
    """SQL for one line: policies (endorsements folded), then fill / flag / link totals by year."""
    key = [k[r] for r in ("office", "dept", "class", "ptype", "source", "pol_no", "year") if r in k]
    pkey = " || '-' || ".join(f'"{c}"' for c in key)
    m = lambda col: f'MAX("{col}")' if col else "NULL"  # noqa: E731
    policies = f"""
    SELECT {pkey} AS PKEY, TRIM(TO_CHAR("{k['pol_no']}")) AS PNO, "{k['year']}" AS PY,
           {m(k.get('class'))} AS CLS, {m(k.get('dept'))} AS DEPT,
           {f'MIN("{k["start"]}")' if 'start' in k else 'NULL'} AS ST,
           {m(k.get('end'))} AS ED,
           {m(cfg.get('prev'))} AS PREV, TRIM(TO_CHAR({m(cfg.get('orig'))})) AS ORIG,
           {m(cfg.get('orig_year'))} AS ORIG_YEAR, {m(cfg.get('flag'))} AS FLAG,
           TRIM(TO_CHAR({m(k.get('cust'))})) AS CUST
    FROM   "{cfg['owner']}"."{cfg['table']}"
    WHERE  "{k['pol_no']}" IS NOT NULL
    GROUP  BY {", ".join(f'"{c}"' for c in key)}"""
    sql = {}
    sql["years"] = f"""
    WITH p AS ({policies})
    SELECT PY AS POLICY_YEAR, DEPT, COUNT(*) AS POLICIES, MIN(ST) AS FIRST_START, MAX(ST) AS LAST_START
    FROM p GROUP BY PY, DEPT ORDER BY 1, 2"""
    sql["fill"] = f"""
    WITH p AS ({policies})
    SELECT PY AS POLICY_YEAR, COUNT(*) AS POLICIES,
           COUNT(PREV) AS PREV_REN_ID_FILLED, COUNT(ORIG) AS ORIGINAL_POL_NO_FILLED,
           COUNT(ORIG_YEAR) AS ORIGINAL_POL_YEAR_FILLED,
           SUM(CASE WHEN REGEXP_LIKE(ORIG, '^[0-9]+$') THEN 1 ELSE 0 END) AS ORIG_ALL_DIGITS,
           ROUND(AVG(LENGTH(ORIG)), 1) AS ORIG_AVG_LENGTH,
           SUM(CASE WHEN ORIG = PNO THEN 1 ELSE 0 END) AS ORIG_EQUALS_OWN_POL_NO,
           COUNT(CUST) AS CUSTOMER_FILLED, APPROX_COUNT_DISTINCT(CUST) AS CUSTOMERS
    FROM p WHERE PY >= {window['first']} GROUP BY PY ORDER BY 1"""
    sql["flags"] = f"""
    WITH p AS ({policies})
    SELECT PY AS POLICY_YEAR, FLAG AS RENEWAL_FLAG, COUNT(*) AS POLICIES,
           COUNT(ORIG) AS WITH_ORIGINAL_POL_NO, COUNT(PREV) AS WITH_PREV_REN_ID
    FROM p WHERE PY >= {window['first']} GROUP BY PY, FLAG ORDER BY 1, 2"""
    if "start" in k and "end" in k:
        b, a = window["before"], window["after"]
        sql["test"] = f"""
    WITH p AS ({policies}),
    o AS (SELECT ORIG, MAX(ST) AS ORIG_LAST_ST FROM p WHERE ORIG IS NOT NULL AND ORIG <> PNO GROUP BY ORIG),
    w AS (SELECT p.*,
                 LEAD(ST)   OVER (PARTITION BY CUST, CLS ORDER BY ST, PY) AS NST,
                 LEAD(PNO)  OVER (PARTITION BY CUST, CLS ORDER BY ST, PY) AS NPNO,
                 LEAD(ORIG) OVER (PARTITION BY CUST, CLS ORDER BY ST, PY) AS NORIG
          FROM p WHERE ED IS NOT NULL),
    f AS (SELECT w.*, o.ORIG_LAST_ST,
                 CASE WHEN CUST IS NOT NULL AND NST BETWEEN ED - {b} AND ED + {a} THEN 1 ELSE 0 END AS CUST_RENEWED
          FROM w LEFT JOIN o ON o.ORIG = w.PNO)
    SELECT EXTRACT(YEAR FROM ED) AS EXPIRY_YEAR, COUNT(*) AS EXPIRING,
           SUM(CASE WHEN CUST IS NOT NULL THEN 1 ELSE 0 END) AS WITH_CUSTOMER,
           SUM(CUST_RENEWED) AS CUSTOMER_LINK_RENEWED,
           SUM(CASE WHEN CUST_RENEWED = 1 AND NPNO = PNO THEN 1 ELSE 0 END) AS RENEWED_SAME_POL_NO,
           SUM(CASE WHEN CUST_RENEWED = 1 AND NORIG = PNO THEN 1 ELSE 0 END) AS RENEWED_ORIG_POINTS_BACK,
           SUM(CASE WHEN ORIG_LAST_ST > ST THEN 1 ELSE 0 END) AS NAMED_AS_ORIGINAL_LATER,
           SUM(CASE WHEN NST > ED + {a} THEN 1 ELSE 0 END) AS CUSTOMER_RETURNED_LATER
    FROM f WHERE ED >= DATE '{window['first_exp']}-01-01'
               AND ED < (SELECT MAX(ST) FROM p) - {a}   -- later expiries can't show a renewal yet
    GROUP BY EXTRACT(YEAR FROM ED) ORDER BY 1"""
    return sql


def run_lines(conn, out_dir: Path, window: dict) -> None:
    """Renewal fields of fire, general, marine cargo and medical. Writes renewal_lines_*.csv."""
    picks, frames = [], {n: [] for n in ("years", "fill", "flags", "test")}
    for i, cfg in enumerate(LINES, 1):
        name = f"{cfg['owner']}.{cfg['table']}"
        cols = query(conn, f"SELECT COLUMN_NAME, DATA_TYPE FROM ALL_TAB_COLUMNS "
                           f"WHERE OWNER = '{cfg['owner']}' AND TABLE_NAME = '{cfg['table']}' ORDER BY COLUMN_ID")
        if cols.empty:
            print(f"  {i}/{len(LINES)} {name}: not readable, skipped")
            continue
        present = set(cols["COLUMN_NAME"])
        for f in ("prev", "orig", "orig_year", "flag"):
            if cfg.get(f) and cfg[f] not in present:
                print(f"      {cfg[f]} not in {name}, ignored")
                cfg = {**cfg, f: None}
        k = pick_columns(cols, cfg["prefix"])
        picks.append(dict(LINE=cfg["line"], TABLE=name, N_COLUMNS=len(cols),
                          **{r.upper(): k.get(r, "") for r in PICK},
                          **{f.upper(): cfg.get(f) or "" for f in ("prev", "orig", "orig_year", "flag")}))
        print(f"  {i}/{len(LINES)} {name}: " + ", ".join(f"{r}={c}" for r, c in k.items()))
        if "pol_no" not in k or "year" not in k:
            print("      no policy number / year column found; columns saved, tests skipped")
            cols.assign(TABLE=name).to_csv(out_dir / f"renewal_lines_cols_{cfg['table']}.csv", index=False)
            continue
        if "start" not in k or "end" not in k:
            print("      no start / end date column found; link test skipped")
            cols.assign(TABLE=name).to_csv(out_dir / f"renewal_lines_cols_{cfg['table']}.csv", index=False)
        for part, sql in lines_sql(cfg, k, window).items():
            try:
                df = query(conn, sql)
            except Exception as e:  # keep going with the other lines
                print(f"      {part}: failed ({str(e).splitlines()[0][:120]})")
                continue
            df.insert(0, "LINE", cfg["line"])
            frames[part].append(df)

    pd.DataFrame(picks).to_csv(out_dir / "renewal_lines_columns.csv", index=False)
    res = {}
    for part, fs in frames.items():
        res[part] = pd.concat(fs, ignore_index=True) if fs else pd.DataFrame()
        res[part].to_csv(out_dir / f"renewal_lines_{part}.csv", index=False)

    pd.set_option("display.width", 220)
    pct = "{:.1%}".format
    if not res["fill"].empty:
        f = res["fill"]
        for c in ("PREV_REN_ID_FILLED", "ORIGINAL_POL_NO_FILLED", "CUSTOMER_FILLED"):
            f[c.replace("_FILLED", "_SHARE")] = f[c] / f["POLICIES"]
        print("\nShare of policies with each link field filled:")
        print(f[["LINE", "POLICY_YEAR", "POLICIES", "PREV_REN_ID_SHARE", "ORIGINAL_POL_NO_SHARE",
                 "ORIG_EQUALS_OWN_POL_NO", "CUSTOMER_SHARE"]].to_string(
            index=False, formatters={c: pct for c in ("PREV_REN_ID_SHARE", "ORIGINAL_POL_NO_SHARE", "CUSTOMER_SHARE")}))
    if not res["test"].empty:
        t = res["test"]
        t["CUSTOMER_RENEWAL_RATE"] = t["CUSTOMER_LINK_RENEWED"] / t["EXPIRING"]
        t["ORIGINAL_LINK_RATE"] = t["NAMED_AS_ORIGINAL_LATER"] / t["EXPIRING"]
        print(f"\nRenewal inside -{window['before']}/+{window['after']} days (customer ID + class), "
              "and share later named as an original policy:")
        print(t[["LINE", "EXPIRY_YEAR", "EXPIRING", "CUSTOMER_RENEWAL_RATE", "RENEWED_ORIG_POINTS_BACK",
                 "ORIGINAL_LINK_RATE"]].to_string(
            index=False, formatters={"CUSTOMER_RENEWAL_RATE": pct, "ORIGINAL_LINK_RATE": pct}))
    print(f"\nSaved renewal_lines_*.csv to {out_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--before", type=int, default=30, help="days before expiry a renewal may start")
    ap.add_argument("--after", type=int, default=60, help="days after expiry a renewal may start")
    ap.add_argument("--first-expiry-year", type=int, default=2020)
    ap.add_argument("--links-only", action="store_true", help="skip the discovery steps (already saved)")
    ap.add_argument("--gaps", action="store_true",
                    help="only the gap check: when each expiring vehicle is next insured, and how many "
                         "policy years each chassis appears in")
    ap.add_argument("--codes", action="store_true",
                    help="only list office and class codes of both tables side by side")
    ap.add_argument("--year", type=int, default=2024, help="policy year for --codes")
    ap.add_argument("--price", action="store_true",
                    help="only renewal against price change (for tools/fit_price_sensitivity.py)")
    ap.add_argument("--notices", action="store_true",
                    help="with --price: also the offered premiums in CAR_RENEWAL_NOTICE (one more long pass)")
    ap.add_argument("--lines", action="store_true",
                    help="only the renewal fields of fire, general, marine cargo and medical")
    args = ap.parse_args()
    window = dict(before=args.before, after=args.after, first=FIRST_YEAR, first_exp=args.first_expiry_year)

    out_dir = (S.raw_dir or ROOT / "data" / "raw") / "checks"
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = _extract.connect()
    print(f"Connected (Oracle {conn.version}). Read-only queries, totals and metadata only.")
    if args.lines:
        with conn:
            run_lines(conn, out_dir, window)
        return
    if args.gaps:
        with conn:
            run_gaps(conn, out_dir, window)
        return
    if args.codes:
        with conn:
            run_codes(conn, out_dir, args.year)
        return
    if args.price:
        with conn:
            run_price(conn, out_dir, window, args.notices)
        return
    with conn:
        if not args.links_only:
            discovery(conn, out_dir)

        print("  1/3 CAR_INS_ENDT renewal fields...")
        ins = table_profile(conn, "CARCDE", "CAR_INS_ENDT")
        ins.to_csv(out_dir / "renewal_ins_endt_columns.csv", index=False)
        year_col = next((c for c in ins["COLUMN_NAME"] if c.startswith("ECAR") and c.endswith("POL_YEAR")),
                        next((c for c in ins["COLUMN_NAME"] if c.endswith("POL_YEAR")), None))
        if year_col:
            flags = query(conn, FLAGS_SQL.format(year_col=year_col, first=FIRST_YEAR))
            flags.to_csv(out_dir / "renewal_ins_endt_flags.csv", index=False)
            print(f"      flags by {year_col}: {len(flags)} rows")
        else:
            print("      no *_POL_YEAR column in CAR_INS_ENDT; flags skipped (columns saved)")

        print("  2/3 vehicle link (chassis number)...")
        veh = query(conn, VEHICLE_SQL.format(**window))
        veh["RENEWAL_RATE"] = veh["RENEWED"] / veh["EXPIRING_VEHICLES"]
        veh["WINDOW_DAYS"] = f"-{args.before}/+{args.after}"
        veh.to_csv(out_dir / "renewal_vehicle_test.csv", index=False)

        print("  3/3 policy link (policy number / customer name)...")
        link = query(conn, LINK_SQL.format(**window))
        for c in ("SAME_POLICY_NO", "SAME_CUSTOMER"):
            link[f"{c}_RATE"] = link[c] / link["EXPIRING"]
        link["WINDOW_DAYS"] = f"-{args.before}/+{args.after}"
        link.to_csv(out_dir / "renewal_link_test.csv", index=False)

    pct = "{:.1%}".format
    print("\nVehicles insured again inside the window (chassis number):")
    print(veh[["EXPIRY_YEAR", "EXPIRING_VEHICLES", "RENEWAL_RATE", "RENEWED_SAME_POLICY_NO",
               "RENEWED_SOURCE_CHANGED"]].to_string(index=False, formatters={"RENEWAL_RATE": pct}))
    print("\nPolicies with a next policy inside the window:")
    print(link[["EXPIRY_YEAR", "EXPIRING", "SAME_POLICY_NO_RATE", "SAME_CUSTOMER_RATE"]]
          .to_string(index=False, formatters={c: pct for c in ("SAME_POLICY_NO_RATE", "SAME_CUSTOMER_RATE")}))
    print(f"\nSaved to {out_dir}")


if __name__ == "__main__":
    main()
