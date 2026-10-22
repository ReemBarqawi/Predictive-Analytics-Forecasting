-- policies.sql — motor policies, one row per policy-year.
-- TEMPLATE: replace the schema, table and source column names with the real ones from the
-- warehouse (ask the DBA). Keep the column ALIASES exactly as written: the pipeline relies on
-- them (see pipeline/contract.py). Do not end the statement with a semicolon.
SELECT
    p.POLICY_NO                AS POLICY_NO,
    p.POLICY_YEAR              AS POLICY_YEAR,
    p.CLASS_DESC               AS CLASS_DESC,
    p.POLICY_TYPE_DESC         AS POLICY_TYPE_DESC,
    p.EFFECTIVE_DATE           AS POLICY_EFFECTIVE_DATE,
    p.EXPIRY_DATE              AS POLICY_EXPIRY_DATE,
    p.TOTAL_PREMIUM            AS TOTAL_PREMIUM,
    p.COMMISSION               AS COMMISSION,
    -- recommended
    p.SOURCE_DESC              AS SOURCE_DESC,
    p.OFFICE_NAME              AS OFFICE_NAME,
    p.SUM_INSURED              AS SUM_INSURED,
    p.CLASS_NO                 AS CLASS_NO,
    p.POLICY_TYPE_NO           AS POLICY_TYPE_NO
FROM   SCHEMA_NAME.POLICY_TABLE p
WHERE  p.CLASS_DESC LIKE 'Motor%'
  AND  p.EFFECTIVE_DATE >= DATE '2019-01-01'
