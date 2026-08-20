import pandas as pd
import numpy as np
import os


#load the data
# Load the claims data
claims = pd.read_excel('data/claims_data.xlsx')

# See what you got
print("Shape:", claims.shape)       # (rows, columns)
print("Columns:", list(claims.columns))  # column names
print(claims.head())  # See the first 5 rows
print(claims.dtypes) # See data types for each column (int, float, object/string, datetime)
print(claims.isnull().sum()) # Count missing values per column


# Load underwriting data
uw = pd.read_excel('data/underwriting_data.xlsx')
print("\nUW Shape:", uw.shape)
print(uw.head())
print(uw.isnull().sum())

# Load policy bridge
policies = pd.read_excel('data/policy_bridge.xlsx')
print("Policies Shape:", policies.shape)
print(policies.head())
print(policies.isnull().sum())


#Understand What Each File Contains
# What's in the claims file?
print("=== CLAIMS FILE ===")
print("Unique policies:", claims['POLICY_NO'].nunique())
print("Policy years:", sorted(claims['POLICY_YEAR'].unique()))
print("Departments:", claims['DEPARTMENT_NAME'].unique())
print("Policy types:", claims['POLICY_TYPE_DESC'].unique())
print("Accident types:", claims['ACC_TYPE_DESC'].unique())

# What's in the policy bridge?
print("\n=== POLICY BRIDGE ===")
print("Unique policies:", policies['POLICY_NO'].nunique())
print("Policy years:", sorted(policies['POLICY_YEAR'].unique()))
print("Sources:", policies['SOURCE_DESC'].value_counts())


#Clean the Data

#Handle Missing Financial Values
# In insurance, missing payment = no payment was made = 0
financial_cols = ['EST_OWN', 'EST_TP', 'PAID_OWN', 'PAID_TP']
for col in financial_cols:
    missing = claims[col].isnull().sum()
    print(f"{col}: {missing} missing → filling with 0")
    claims[col] = claims[col].fillna(0)


#Handle Missing Accident Type
# 8 rows have no accident type — mark them rather than drop them
claims['ACC_TYPE_DESC'] = claims['ACC_TYPE_DESC'].fillna('Unknown')
print(f"ACC_TYPE values now: {claims['ACC_TYPE_DESC'].nunique()}")


#Recompute Totals (Safety Check)
# Don't trust pre-computed columns — recompute to be safe
claims['TOTAL_EST'] = claims['EST_OWN'] + claims['EST_TP']
claims['TOTAL_PAID'] = claims['PAID_OWN'] + claims['PAID_TP']

# Compute NET_INCURRED = what the insurer actually lost
claims['NET_INCURRED'] = claims['TOTAL_PAID'] - claims['RECOVERY_AMT']

#Clean the Policy Bridge
# Fill missing agent info
policies['AGENT_NO'] = policies['AGENT_NO'].fillna(-1)
policies['AGENT_NAME'] = policies['AGENT_NAME'].fillna('Unknown')

# Compute how many years each policy was at risk
policies['EXPOSURE_DAYS'] = (
    pd.to_datetime(policies['POLICY_EXPIRY_DATE']) 
    - pd.to_datetime(policies['POLICY_EFFECTIVE_DATE'])
).dt.days

policies['EXPOSURE_YEARS'] = policies['EXPOSURE_DAYS'] / 365.25

# Check the result
print("\nExposure summary:")
print(policies['EXPOSURE_YEARS'].describe())

# Cap at reasonable values (some may be > 1 year due to renewals)
policies['EXPOSURE_YEARS'] = policies['EXPOSURE_YEARS'].clip(lower=0, upper=2)


# Save so you don't have to re-clean every time
claims.to_csv('outputs/claims_cleaned.csv', index=False)
policies.to_csv('outputs/policies_cleaned.csv', index=False)

# Also save severity-only data (claims that actually had a payment)
severity = claims[claims['TOTAL_PAID'] > 0]
severity.to_csv('outputs/severity_data.csv', index=False)

print(f"\nSaved {len(claims)} claims, {len(policies)} policies, {len(severity)} severity records")



# Count how many claims each policy had
claims_per_policy = (
    claims
    .groupby(['POLICY_NO', 'POLICY_YEAR'])
    .agg(
        CLAIM_COUNT=('CLM_NO', 'count'),
        TOTAL_PAID_SUM=('TOTAL_PAID', 'sum'),
    )
    .reset_index()
)

print("Claim count distribution:")
print(claims_per_policy['CLAIM_COUNT'].value_counts().sort_index().head(10))

claims_per_policy.to_csv('outputs/claims_by_policy.csv', index=False)

