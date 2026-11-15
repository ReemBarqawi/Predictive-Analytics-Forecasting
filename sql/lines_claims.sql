-- lines_claims.sql: every line of business except motor, one row per claim. Same keys as claims.sql;
-- the bare policy number and class code come too, for the second claim match in 20_lines_quality.py.
SELECT
    c.OFFICE_NO || '-' || c.DEPARTMENT_NO || '-' || c.COB || '-' || c.POLICY_TYPE
        || '-' || c.SOURCE_NO || '-' || c.POLICY_NO
        || '-' || c.CLM_YEAR || '-' || c.CLM_NO                AS CLM_NO,
    c.OFFICE_NO || '-' || c.DEPARTMENT_NO || '-' || c.COB || '-' || c.POLICY_TYPE
        || '-' || c.SOURCE_NO || '-' || c.POLICY_NO            AS POLICY_NO,
    c.POLICY_YEAR                           AS POLICY_YEAR,
    c.POLICY_NO                             AS POLICY_NUMBER,
    c.DEPARTMENT_NAME                       AS DEPARTMENT,
    c.COB                                   AS CLASS_NO,
    c.COB_DESC                              AS CLASS_DESC,
    c.POLICY_TYPE_DESC                      AS POLICY_TYPE_DESC,
    c.ACC_DATE                              AS ACC_DATE,
    c.CLOSE_DATE                            AS CLOSE_DATE,
    c.CLM_YEAR                              AS CLM_YEAR,
    c.ACC_TYPE_DESC                         AS ACC_TYPE_DESC,
    c.TOTAL_EST                             AS TOTAL_EST,
    c.TOTAL_PAID                            AS TOTAL_PAID,
    c.RECOVERY_AMT                          AS RECOVERY_AMT
FROM   DTWCDE.V_CLM_DATA c
WHERE  c.DEPARTMENT_NAME <> 'Motor'
  AND  c.POLICY_YEAR >= {first_policy_year}   -- [oracle] first_policy_year in config.toml (0 = all history); same as lines_policies.sql
