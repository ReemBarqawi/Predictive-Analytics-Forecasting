-- policies.sql: motor policies, one row per policy-year (endorsements and cancellations summed).
-- Key agreed with the DBA: office + department + class + policy type + source + policy no + year.
SELECT
    u.VUW_OFFICE || '-' || u.VUW_DEPT_NO || '-' || u.VUW_CLASS || '-' || u.VUW_POL_TYPE
        || '-' || u.VUW_SOURCE || '-' || u.VUW_POL_NO          AS POLICY_NO,
    u.VUW_POL_YEAR                          AS POLICY_YEAR,
    MAX(u.VUW_CLASS_DESC)                   AS CLASS_DESC,
    MAX(u.VUW_POL_TYPE_DESC)                AS POLICY_TYPE_DESC,
    MIN(u.VUW_EFFECTIVE_DT)                 AS POLICY_EFFECTIVE_DATE,
    MAX(u.VUW_EXP_DT)                       AS POLICY_EXPIRY_DATE,
    SUM(u.VUW_TOTAL_PREM)                   AS TOTAL_PREMIUM,
    SUM(u.VUW_AGENT_COMM_LAMT)              AS COMMISSION,
    -- recommended
    MAX(u.VUW_SOURCE_DESC)                  AS SOURCE_DESC,
    MAX(u.OFFICE_NAME)                      AS OFFICE_NAME,
    MAX(u.VUW_SI)                           AS SUM_INSURED,
    MAX(u.VUW_CLASS)                        AS CLASS_NO,
    MAX(u.VUW_POL_TYPE)                     AS POLICY_TYPE_NO,
    MAX(u.VUW_BRANCH)                       AS BRANCH_NO,
    MAX(u.VUW_BRANCH_DESC)                  AS BRANCH_NAME,
    MAX(u.VUW_AGENT_NO)                     AS AGENT_NO,
    MAX(u.AGENT_NAME)                       AS AGENT_NAME,
    SUM(u.VUW_GROSS_PREM)                   AS GROSS_PREMIUM,
    SUM(u.VUW_NET_PREM)                     AS NET_PREMIUM
FROM   DTWCDE.V_UW_DATA u
WHERE  u.VUW_DEPT_DESC = 'Motor'
  AND  u.VUW_POL_YEAR >= {first_policy_year}   -- [oracle] first_policy_year in config.toml (0 = all history)
GROUP  BY u.VUW_OFFICE, u.VUW_DEPT_NO, u.VUW_CLASS, u.VUW_POL_TYPE,
          u.VUW_SOURCE, u.VUW_POL_NO, u.VUW_POL_YEAR