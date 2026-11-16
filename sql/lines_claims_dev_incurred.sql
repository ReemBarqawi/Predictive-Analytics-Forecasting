-- lines_claims_dev_incurred.sql: incurred (estimate) development triangle of every line except motor.
-- One row per department x class x accident quarter x development quarter. Same method as claims_dev_incurred.sql,
-- from the general claims system's reserve history CLMCDE.CLM_RESTMT_OS_DET (motor's is CARCDE.CAR_RESTMT_OS_DET):
-- each revision is the new estimate minus the previous one for the same claim x estimate type, so the rows
-- sum to the claim's latest estimate. Used by pipeline/20_lines_quality.py to develop recent years to ultimate.
WITH r AS (
  SELECT d.CROD_CLASS_OF_BUSINESS, d.CROD_CLM_YEAR, d.CROD_CLM_NO, d.CROD_EVAL_DT,
         d.CROD_EST_VAL - LAG(d.CROD_EST_VAL, 1, 0) OVER (
             PARTITION BY d.CROD_CLASS_OF_BUSINESS, d.CROD_CLM_YEAR, d.CROD_CLM_NO, d.CROD_EST_TYPE
             ORDER BY d.CROD_EVAL_DT, d.CROD_REV_EST_SRL)               AS INCURRED_CHANGE
  FROM   CLMCDE.CLM_RESTMT_OS_DET d
)
SELECT c.DEPARTMENT_NAME                                               AS DEPARTMENT,
       c.COB                                                           AS CLASS_NO,
       TRUNC(c.ACC_DATE, 'Q')                                          AS ACC_QUARTER,
       GREATEST(0, MONTHS_BETWEEN(TRUNC(r.CROD_EVAL_DT, 'Q'),
                                  TRUNC(c.ACC_DATE, 'Q')) / 3)         AS DEV_QUARTER,
       SUM(r.INCURRED_CHANGE)                                          AS INCURRED_CHANGE
FROM   r
JOIN   DTWCDE.V_CLM_DATA c
  ON   c.COB = r.CROD_CLASS_OF_BUSINESS AND c.CLM_YEAR = r.CROD_CLM_YEAR AND c.CLM_NO = r.CROD_CLM_NO
WHERE  c.DEPARTMENT_NAME <> 'Motor'
GROUP  BY c.DEPARTMENT_NAME, c.COB, TRUNC(c.ACC_DATE, 'Q'),
          GREATEST(0, MONTHS_BETWEEN(TRUNC(r.CROD_EVAL_DT, 'Q'), TRUNC(c.ACC_DATE, 'Q')) / 3)
ORDER  BY 1, 2, 3, 4
