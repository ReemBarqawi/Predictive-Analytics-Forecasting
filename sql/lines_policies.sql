-- lines_policies.sql: every line of business except motor, one row per policy-year (endorsements and
-- cancellations summed). Same 7-part key as policies.sql. The bare policy number and class code come too,
-- for the second claim match (number + year + class) in pipeline/20_lines_quality.py.
SELECT
    u.VUW_OFFICE || '-' || u.VUW_DEPT_NO || '-' || u.VUW_CLASS || '-' || u.VUW_POL_TYPE
        || '-' || u.VUW_SOURCE || '-' || u.VUW_POL_NO          AS POLICY_NO,
    u.VUW_POL_YEAR                          AS POLICY_YEAR,
    MAX(u.VUW_POL_NO)                       AS POLICY_NUMBER,
    MAX(u.VUW_DEPT_DESC)                    AS DEPARTMENT,
    MAX(u.VUW_CLASS)                        AS CLASS_NO,
    MAX(u.VUW_CLASS_DESC)                   AS CLASS_DESC,
    MAX(u.VUW_POL_TYPE_DESC)                AS POLICY_TYPE_DESC,
    MAX(u.VUW_SOURCE_DESC)                  AS SOURCE_DESC,
    MAX(u.OFFICE_NAME)                      AS OFFICE_NAME,
    MIN(u.VUW_EFFECTIVE_DT)                 AS POLICY_EFFECTIVE_DATE,
    MAX(u.VUW_EXP_DT)                       AS POLICY_EXPIRY_DATE,
    SUM(u.VUW_TOTAL_PREM)                   AS TOTAL_PREMIUM,
    SUM(u.VUW_GROSS_PREM)                   AS GROSS_PREMIUM,
    SUM(u.VUW_NET_PREM)                     AS NET_PREMIUM,
    SUM(u.VUW_AGENT_COMM_LAMT)              AS COMMISSION,
    MAX(u.VUW_SI)                           AS SUM_INSURED,
    COUNT(*)                                AS ROWS_IN_SOURCE
FROM   DTWCDE.V_UW_DATA u
WHERE  u.VUW_DEPT_DESC <> 'Motor'
  AND  u.VUW_POL_YEAR >= {first_policy_year}   -- [oracle] first_policy_year in config.toml (0 = all history)
GROUP  BY u.VUW_OFFICE, u.VUW_DEPT_NO, u.VUW_CLASS, u.VUW_POL_TYPE,
          u.VUW_SOURCE, u.VUW_POL_NO, u.VUW_POL_YEAR
