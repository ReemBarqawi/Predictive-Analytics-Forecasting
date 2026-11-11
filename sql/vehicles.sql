-- vehicles.sql: motor vehicles, one row per vehicle x policy-year (endorsements folded).
-- Source: CARCDE.VCAR_MAX_VEHICLE_ENDT (not DTWCDE; allowed for now, see docs/MODEL_IMPROVEMENT_PLAN.md A2).
-- Policy key = the same 7 parts as policies.sql (vehicle VVCL_POL_OFFICE = policy VUW_OFFICE, checked 2026-11-10).
-- Privacy: the chassis number leaves Oracle only as a one-way hash (it links a car to its next policy),
-- and the driver's birth date only as an age in whole years at the start of cover.
-- Years: the last [oracle] vehicles_years policy years up to the data's end (the latest motor accident
-- year; the view is slow: all history ran for over an hour). Renewal (step 17) needs about two years,
-- the GLMs their training window.
-- Used by: 17_renewal.py (renewal rates); later the GLM vehicle factors (plan D1-D2).
WITH last_year AS (
    SELECT EXTRACT(YEAR FROM MAX(c.ACC_DATE)) AS Y   -- the data's end, not today (backups end early)
    FROM   DTWCDE.V_CLM_DATA c
    WHERE  c.DEPARTMENT_NAME = 'Motor'),
v AS (
    SELECT UPPER(TRIM(e.VVCL_CHASSIS_NO))                                       AS CH,
           e.VVCL_POL_OFFICE || '-' || e.VVCL_DEPT_NO || '-' || e.VVCL_CLASS_OF_BUSINESS || '-' || e.VVCL_POLICY_TYPE
               || '-' || e.VVCL_SOURCE || '-' || e.VVCL_POL_NO                  AS POLICY_NO,
           e.VVCL_POL_YEAR                                                      AS POLICY_YEAR,
           MIN(e.VVCL_INS_ST_DT)                                                AS COVER_START,
           MAX(e.VVCL_INS_ED_DT)                                                AS COVER_END,
           MAX(e.VVCL_STATUS)                                                   AS STATUS,
           MAX(e.VVCL_STATUS_DT)                                                AS STATUS_DATE,
           SUM(e.VVCL_GROSS_PREM)                                               AS GROSS_PREMIUM,
           MAX(e.VVCL_YEAR_OF_MAKE)                                             AS YEAR_OF_MAKE,
           MAX(e.VVCL_CLASS_OF_USE)                                             AS CLASS_OF_USE,
           MAX(e.VVCL_VEHICLE_MAKE)                                             AS VEHICLE_MAKE,
           MAX(e.VVCL_TYPE_OF_BODY)                                             AS BODY_TYPE,
           MAX(e.VVCL_SI)                                                       AS SUM_INSURED,
           MAX(FLOOR(MONTHS_BETWEEN(e.VVCL_INS_ST_DT, e.VVCL_CUST_BIRTH_DT) / 12)) AS DRIVER_AGE
    FROM   CARCDE.VCAR_MAX_VEHICLE_ENDT e, last_year
    WHERE  e.VVCL_POL_YEAR > last_year.Y - {vehicles_years}   -- [oracle] vehicles_years in config.toml
      AND  e.VVCL_POL_YEAR >= {first_policy_year}
      AND  LENGTH(TRIM(e.VVCL_CHASSIS_NO)) >= 8
    GROUP  BY UPPER(TRIM(e.VVCL_CHASSIS_NO)), e.VVCL_POL_OFFICE, e.VVCL_DEPT_NO, e.VVCL_CLASS_OF_BUSINESS,
              e.VVCL_POLICY_TYPE, e.VVCL_SOURCE, e.VVCL_POL_NO, e.VVCL_POL_YEAR)
SELECT RAWTOHEX(STANDARD_HASH(CH, 'SHA1')) AS VEHICLE_ID,          -- hashed once per vehicle-policy
       POLICY_NO, POLICY_YEAR, COVER_START, COVER_END, STATUS, STATUS_DATE, GROSS_PREMIUM,
       YEAR_OF_MAKE, CLASS_OF_USE, VEHICLE_MAKE, BODY_TYPE, SUM_INSURED, DRIVER_AGE
FROM   v
