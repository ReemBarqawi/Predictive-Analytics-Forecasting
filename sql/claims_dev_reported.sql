-- claims_dev_reported.sql: motor reported-claim-count development triangle.
-- One row per accident quarter x development quarter x class x policy type; a claim counts in
-- the quarter of its first reserve record (MIN(CROD_EVAL_DT) in CARCDE.CAR_RESTMT_OS_DET).
-- Used by 07_forecast_seasonal to develop recent accident months' claim counts to ultimate.
WITH f AS (
  SELECT CROD_OFFICE, CROD_CLASS_OF_BUSINESS, CROD_POLICY_TYPE, CROD_CLM_YEAR, CROD_CLM_NO,
         MIN(CROD_EVAL_DT) AS FIRST_DT
  FROM   CARCDE.CAR_RESTMT_OS_DET
  GROUP  BY CROD_OFFICE, CROD_CLASS_OF_BUSINESS, CROD_POLICY_TYPE, CROD_CLM_YEAR, CROD_CLM_NO
)
SELECT TRUNC(c.ACC_DATE, 'Q')                                          AS ACC_QUARTER,
       GREATEST(0, MONTHS_BETWEEN(TRUNC(f.FIRST_DT, 'Q'),
                                  TRUNC(c.ACC_DATE, 'Q')) / 3)         AS DEV_QUARTER,
       c.COB, c.POLICY_TYPE,
       COUNT(*)                                                        AS CLAIMS_REPORTED
FROM   f
JOIN   DTWCDE.V_CLM_DATA c
  ON   c.OFFICE_NO = f.CROD_OFFICE AND c.COB = f.CROD_CLASS_OF_BUSINESS
 AND   c.POLICY_TYPE = f.CROD_POLICY_TYPE
 AND   c.CLM_YEAR = f.CROD_CLM_YEAR AND c.CLM_NO = f.CROD_CLM_NO
WHERE  c.DEPARTMENT_NAME = 'Motor'
GROUP  BY TRUNC(c.ACC_DATE, 'Q'),
          GREATEST(0, MONTHS_BETWEEN(TRUNC(f.FIRST_DT, 'Q'), TRUNC(c.ACC_DATE, 'Q')) / 3),
          c.COB, c.POLICY_TYPE
ORDER  BY 1, 2, 3, 4;
