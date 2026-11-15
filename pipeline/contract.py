"""
contract.py — the columns the warehouse extract must deliver (one row per policy-year /
one row per claim). The SQL files in sql/ must return these names (use column aliases).

REQUIRED columns must exist; the prepare step stops with a clear message if one is missing.
RECOMMENDED columns improve the models and the dashboard; when absent they are filled with
a documented default and listed in the run log.
"""

POLICIES = {
    "required": {
        "POLICY_NO": "policy number (with POLICY_YEAR, unique per row)",
        "POLICY_YEAR": "underwriting / renewal year",
        "CLASS_DESC": "line of business, e.g. 'Motor Private'",
        "POLICY_TYPE_DESC": "cover type, e.g. 'Comprehensive', 'Third Party'",
        "POLICY_EFFECTIVE_DATE": "cover start date",
        "POLICY_EXPIRY_DATE": "cover end date",
        "TOTAL_PREMIUM": "written premium for the period (negative = cancellation)",
        "COMMISSION": "commission paid on the policy",
    },
    "recommended": {
        "SOURCE_DESC": ("distribution channel (broker, direct, ...); pricing feature", "Unknown"),
        "OFFICE_NAME": ("issuing office; mapped to CITY/REGION/ZONE", None),
        "ZONE": ("geographic zone, if the warehouse already has it", None),
        "SUM_INSURED": ("sum insured (SAR); banded into a pricing feature", None),
        "CLASS_NO": ("class code", None),
        "POLICY_TYPE_NO": ("cover type code", None),
        "BRANCH_NO": ("branch code", None),
        "BRANCH_NAME": ("branch name", None),
        "CLIENT_NO": ("client number", None),
        "AGENT_NO": ("agent / broker code", None),
        "AGENT_NAME": ("agent / broker name", None),
        "GROSS_PREMIUM": ("gross premium", None),
        "NET_PREMIUM": ("net premium", None),
    },
}

CLAIMS = {
    "required": {
        "CLM_NO": "claim number (unique per row)",
        "POLICY_NO": "policy the claim belongs to",
        "POLICY_YEAR": "policy year the claim belongs to",
        "ACC_DATE": "accident / loss date",
        "TOTAL_PAID": "amount paid to date",
    },
    "recommended": {
        "CASE_RESERVE": ("outstanding case reserve; else TOTAL_EST - TOTAL_PAID; else 0", None),
        "TOTAL_EST": ("total estimate (paid + outstanding)", None),
        "IS_CLOSED": ("1/0 or Y/N closed flag; else derived from CLOSE_DATE", None),
        "CLOSE_DATE": ("settlement date", None),
        "REPORT_DATE": ("notification date", None),
        "CLM_YEAR": ("claim year; else year of ACC_DATE", None),
        "ACC_TYPE_DESC": ("accident type (reporting only, never a pricing feature)", None),
        "RECOVERY_AMT": ("recoveries / subrogation", None),
    },
}


# ---------------------------------------------------------------------------------------------------------------
# Every extract (sql/<name>.sql -> <raw_dir>/<name>.parquet): what it must hold, checked by
# pipeline/23_validate_extract.py before anything is built from it. A company's data either passes or gets a list
# of what to fix. types: text, int, number, date. "needed": the run cannot go on without the file; otherwise the
# consequence of its absence is logged. "key": the columns that identify a row (duplicates are reported).
# ---------------------------------------------------------------------------------------------------------------
EXTRACTS = {
    "policies": {
        "needed": True, "what": "motor policies, one row per policy-year",
        "key": ["POLICY_NO", "POLICY_YEAR"],
        "types": {"POLICY_NO": "text", "POLICY_YEAR": "int", "CLASS_DESC": "text", "POLICY_TYPE_DESC": "text",
                  "POLICY_EFFECTIVE_DATE": "date", "POLICY_EXPIRY_DATE": "date", "TOTAL_PREMIUM": "number",
                  "COMMISSION": "number", "SUM_INSURED": "number", "CLASS_NO": "int", "GROSS_PREMIUM": "number",
                  "NET_PREMIUM": "number", "DEPARTMENT_NAME": "text"},
        "required": list(POLICIES["required"]), "recommended": list(POLICIES["recommended"]) + ["DEPARTMENT_NAME"],
    },
    "claims": {
        "needed": True, "what": "motor claims, one row per claim",
        "key": ["CLM_NO"],
        "types": {"CLM_NO": "text", "POLICY_NO": "text", "POLICY_YEAR": "int", "ACC_DATE": "date",
                  "TOTAL_PAID": "number", "TOTAL_EST": "number", "CLOSE_DATE": "date", "RECOVERY_AMT": "number"},
        "required": list(CLAIMS["required"]), "recommended": list(CLAIMS["recommended"]),
    },
    "claims_dev_incurred": {
        "needed": False, "what": "incurred cost triangle (accident x development quarter)",
        "without": "recent motor claims are not developed: the forecast and reserving understate them",
        "key": ["ACC_QUARTER", "DEV_QUARTER", "COB", "POLICY_TYPE", "PARTY"],
        "types": {"ACC_QUARTER": "date", "DEV_QUARTER": "int", "INCURRED_CHANGE": "number"},
        "required": ["ACC_QUARTER", "DEV_QUARTER", "INCURRED_CHANGE"], "recommended": ["COB", "POLICY_TYPE", "PARTY"],
    },
    "claims_dev_reported": {
        "needed": False, "what": "reported claim count triangle (accident x development quarter)",
        "without": "recent claim counts are not developed: GLM exposure and the forecast take them as complete",
        "key": ["ACC_QUARTER", "DEV_QUARTER", "COB", "POLICY_TYPE"],
        "types": {"ACC_QUARTER": "date", "DEV_QUARTER": "int", "CLAIMS_REPORTED": "number"},
        "required": ["ACC_QUARTER", "DEV_QUARTER", "CLAIMS_REPORTED"], "recommended": ["COB", "POLICY_TYPE"],
    },
    "vehicles": {
        "needed": False, "what": "motor vehicles, one row per vehicle x policy-year",
        "without": "no cars per policy (one car each), no vehicle rating factors, no measured renewal (config falls back)",
        "key": ["VEHICLE_ID", "POLICY_NO", "POLICY_YEAR"],
        "types": {"VEHICLE_ID": "text", "POLICY_NO": "text", "POLICY_YEAR": "int", "COVER_START": "date",
                  "COVER_END": "date", "YEAR_OF_MAKE": "number", "SUM_INSURED": "number", "DRIVER_AGE": "number"},
        "required": ["VEHICLE_ID", "POLICY_NO", "POLICY_YEAR", "COVER_START", "COVER_END"],
        "recommended": ["YEAR_OF_MAKE", "DRIVER_AGE", "CLASS_OF_USE", "SUM_INSURED", "GROSS_PREMIUM"],
    },
    "lines_policies": {
        "needed": False, "what": "other lines' policies, one row per policy-year",
        "without": "motor only: no other lines on the pages",
        "key": ["POLICY_NO", "POLICY_YEAR"],
        "types": {"POLICY_NO": "text", "POLICY_YEAR": "int", "POLICY_NUMBER": "int", "DEPARTMENT": "text",
                  "CLASS_NO": "int", "CLASS_DESC": "text", "POLICY_EFFECTIVE_DATE": "date", "POLICY_EXPIRY_DATE": "date",
                  "TOTAL_PREMIUM": "number", "GROSS_PREMIUM": "number", "COMMISSION": "number", "SUM_INSURED": "number"},
        "required": ["POLICY_NO", "POLICY_YEAR", "DEPARTMENT", "CLASS_NO", "CLASS_DESC", "POLICY_EFFECTIVE_DATE",
                     "POLICY_EXPIRY_DATE", "TOTAL_PREMIUM"],
        "recommended": ["POLICY_NUMBER", "POLICY_TYPE_DESC", "SOURCE_DESC", "OFFICE_NAME", "GROSS_PREMIUM",
                        "NET_PREMIUM", "COMMISSION", "SUM_INSURED"],
    },
    "lines_claims": {
        "needed": False, "what": "other lines' claims, one row per claim",
        "without": "other lines show premium only",
        "key": ["CLM_NO"],
        "types": {"CLM_NO": "text", "POLICY_NO": "text", "POLICY_YEAR": "int", "POLICY_NUMBER": "int",
                  "DEPARTMENT": "text", "CLASS_NO": "int", "CLASS_DESC": "text", "ACC_DATE": "date",
                  "TOTAL_EST": "number", "TOTAL_PAID": "number"},
        "required": ["CLM_NO", "POLICY_NO", "POLICY_YEAR", "DEPARTMENT", "CLASS_NO", "CLASS_DESC", "TOTAL_EST",
                     "TOTAL_PAID"],
        "recommended": ["POLICY_NUMBER", "POLICY_TYPE_DESC", "ACC_DATE", "CLOSE_DATE", "RECOVERY_AMT"],
    },
}

# Method constants: when a problem stops the run (STOP) or is only reported (WARN). Shares are of the rows.
VALIDATION = {
    "date_range": ("1900-01-01", "2100-12-31"),   # outside: a typing error
    "stop_bad_required": 0.05,     # a required column with more unreadable or empty values than this: STOP
    "warn_bad": 0.001,             # any typed column with more than this: WARN
    "stop_duplicate_key": 0.01,    # duplicate keys above this share: STOP (results would double count)
    "stop_claim_match": 0.50,      # claims finding their policy (key + year) below this: STOP
    "warn_claim_match": 0.90,      #                                           below this: WARN
    "warn_vehicle_match": 0.90,    # vehicles finding their policy-year below this: WARN
}
