-- claims.sql: motor claims, one row per claim (checked: 355,126 rows = 355,126 claims).
-- Policy key = same 7 parts as policies.sql; claim key = policy key + claim year + claim no.
SELECT
    c.OFFICE_NO || '-' || c.DEPARTMENT_NO || '-' || c.COB || '-' || c.POLICY_TYPE
        || '-' || c.SOURCE_NO || '-' || c.POLICY_NO
        || '-' || c.CLM_YEAR || '-' || c.CLM_NO                AS CLM_NO,
    c.OFFICE_NO || '-' || c.DEPARTMENT_NO || '-' || c.COB || '-' || c.POLICY_TYPE
        || '-' || c.SOURCE_NO || '-' || c.POLICY_NO            AS POLICY_NO,
    c.POLICY_YEAR                           AS POLICY_YEAR,
    c.ACC_DATE                              AS ACC_DATE,
    c.TOTAL_PAID                            AS TOTAL_PAID,
    -- recommended
    c.TOTAL_EST                             AS TOTAL_EST,
    c.CLOSE_DATE                            AS CLOSE_DATE,
    c.CLM_YEAR                              AS CLM_YEAR,
    c.ACC_TYPE_DESC                         AS ACC_TYPE_DESC,
    c.RECOVERY_AMT                          AS RECOVERY_AMT
FROM   DTWCDE.V_CLM_DATA c
WHERE  c.DEPARTMENT_NAME = 'Motor'
  AND  c.POLICY_YEAR >= 2019        -- same window as policies.sql, so every claim can find its policy