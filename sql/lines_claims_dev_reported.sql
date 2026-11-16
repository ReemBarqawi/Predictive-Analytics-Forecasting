-- lines_claims_dev_reported.sql: reported claim count triangle of every line except motor.
-- One row per department x class x accident quarter x development quarter; a claim counts in the quarter of its
-- first reserve record in CLMCDE.CLM_RESTMT_OS_DET (as claims_dev_reported.sql does for motor).
WITH f AS (
  SELECT CROD_CLASS_OF_BUSINESS, CROD_CLM_YEAR, CROD_CLM_NO, MIN(CROD_EVAL_DT) AS FIRST_DT
  FROM   CLMCDE.CLM_RESTMT_OS_DET
  GROUP  BY CROD_CLASS_OF_BUSINESS, CROD_CLM_YEAR, CROD_CLM_NO
)
SELECT c.DEPARTMENT_NAME                                               AS DEPARTMENT,
       c.COB                                                           AS CLASS_NO,
       TRUNC(c.ACC_DATE, 'Q')                                          AS ACC_QUARTER,
       GREATEST(0, MONTHS_BETWEEN(TRUNC(f.FIRST_DT, 'Q'),
                                  TRUNC(c.ACC_DATE, 'Q')) / 3)         AS DEV_QUARTER,
       COUNT(*)                                                        AS CLAIMS_REPORTED
FROM   f
JOIN   DTWCDE.V_CLM_DATA c
  ON   c.COB = f.CROD_CLASS_OF_BUSINESS AND c.CLM_YEAR = f.CROD_CLM_YEAR AND c.CLM_NO = f.CROD_CLM_NO
WHERE  c.DEPARTMENT_NAME <> 'Motor'
GROUP  BY c.DEPARTMENT_NAME, c.COB, TRUNC(c.ACC_DATE, 'Q'),
          GREATEST(0, MONTHS_BETWEEN(TRUNC(f.FIRST_DT, 'Q'), TRUNC(c.ACC_DATE, 'Q')) / 3)
ORDER  BY 1, 2, 3, 4
