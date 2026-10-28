-- claims_dev_incurred.sql: motor incurred (paid + case reserve) development triangle.
-- One row per accident quarter x development quarter x class x policy type x party.
-- Source: reserve history CARCDE.CAR_RESTMT_OS_DET (one row per estimate change, ordered by
-- CROD_SERIAL; DTW monthly claim tables are empty). Each change is the new estimate minus the
-- previous one for the same claim x party x estimate type, so the rows sum to incurred.
-- Used by 07_forecast_seasonal to develop recent accident months to ultimate.
WITH r AS (
  SELECT d.CROD_OFFICE, d.CROD_CLASS_OF_BUSINESS, d.CROD_POLICY_TYPE,
         d.CROD_CLM_YEAR, d.CROD_CLM_NO, d.CROD_TP_SERIAL, d.CROD_EVAL_DT,
         d.CROD_EST_VAL - LAG(d.CROD_EST_VAL, 1, 0) OVER (
             PARTITION BY d.CROD_BRANCH, d.CROD_OFFICE, d.CROD_CLASS_OF_BUSINESS,
                          d.CROD_POLICY_TYPE, d.CROD_CLM_YEAR, d.CROD_CLM_NO,
                          d.CROD_TP_SERIAL, d.CROD_EST_TYPE
             ORDER BY d.CROD_SERIAL)                          AS INCURRED_CHANGE
  FROM   CARCDE.CAR_RESTMT_OS_DET d
)
SELECT TRUNC(c.ACC_DATE, 'Q')                                          AS ACC_QUARTER,
       GREATEST(0, MONTHS_BETWEEN(TRUNC(r.CROD_EVAL_DT, 'Q'),
                                  TRUNC(c.ACC_DATE, 'Q')) / 3)         AS DEV_QUARTER,
       c.COB, c.POLICY_TYPE,
       CASE WHEN r.CROD_TP_SERIAL > 0 THEN 'TP' ELSE 'OWN' END         AS PARTY,
       SUM(r.INCURRED_CHANGE)                                          AS INCURRED_CHANGE
FROM   r
JOIN   DTWCDE.V_CLM_DATA c
  ON   c.OFFICE_NO = r.CROD_OFFICE AND c.COB = r.CROD_CLASS_OF_BUSINESS
 AND   c.POLICY_TYPE = r.CROD_POLICY_TYPE
 AND   c.CLM_YEAR = r.CROD_CLM_YEAR AND c.CLM_NO = r.CROD_CLM_NO
WHERE  c.DEPARTMENT_NAME = 'Motor'
GROUP  BY TRUNC(c.ACC_DATE, 'Q'),
          GREATEST(0, MONTHS_BETWEEN(TRUNC(r.CROD_EVAL_DT, 'Q'), TRUNC(c.ACC_DATE, 'Q')) / 3),
          c.COB, c.POLICY_TYPE,
          CASE WHEN r.CROD_TP_SERIAL > 0 THEN 'TP' ELSE 'OWN' END
ORDER  BY 1, 2, 3, 4, 5;
