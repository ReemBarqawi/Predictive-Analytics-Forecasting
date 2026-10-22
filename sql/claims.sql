-- claims.sql — motor claims, one row per claim.
-- TEMPLATE: replace the schema, table and source column names with the real ones from the
-- warehouse (ask the DBA). Keep the column ALIASES exactly as written: the pipeline relies on
-- them (see pipeline/contract.py). Do not end the statement with a semicolon.
SELECT
    c.CLAIM_NO                 AS CLM_NO,
    c.POLICY_NO                AS POLICY_NO,
    c.POLICY_YEAR              AS POLICY_YEAR,
    c.ACCIDENT_DATE            AS ACC_DATE,
    c.TOTAL_PAID               AS TOTAL_PAID,
    -- recommended
    c.OUTSTANDING_RESERVE      AS CASE_RESERVE,
    c.CLOSE_DATE               AS CLOSE_DATE,
    c.REPORT_DATE              AS REPORT_DATE,
    c.ACC_TYPE_DESC            AS ACC_TYPE_DESC
FROM   SCHEMA_NAME.CLAIM_TABLE c
WHERE  c.ACCIDENT_DATE >= DATE '2019-01-01'
