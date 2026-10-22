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
