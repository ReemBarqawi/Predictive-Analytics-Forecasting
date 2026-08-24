import pandas as pd
import numpy as np

severity = pd.read_csv('outputs/severity_data.csv')

print("Shape:", severity.shape)
print("\nTarget variable (TOTAL_PAID):")
print(severity['TOTAL_PAID'].describe())

# Select features we'll use
features = ['POLICY_TYPE_DESC', 'COB_DESC', 'SOURCE_DESC', 'ACC_TYPE_DESC']

# Check each one
for col in features:
    print(f"\n{col}:")
    print(severity[col].value_counts())


# Group rare accident types into "Other"
# Keep only categories with at least 30 observations
acc_counts = severity['ACC_TYPE_DESC'].value_counts()
print("Before grouping:")
print(acc_counts)

# Categories with fewer than 30 claims → group into "Other"
rare_types = acc_counts[acc_counts < 30].index.tolist()
severity['ACC_TYPE_GROUPED'] = severity['ACC_TYPE_DESC'].replace(rare_types, 'Other')

print("\nAfter grouping:")
print(severity['ACC_TYPE_GROUPED'].value_counts())

# Create dummy variables
# drop_first=True avoids the "dummy variable trap" (multicollinearity)
severity_encoded = pd.get_dummies(
    severity[['TOTAL_PAID', 'POLICY_TYPE_DESC', 'COB_DESC', 'SOURCE_DESC', 'ACC_TYPE_GROUPED']],
    columns=['POLICY_TYPE_DESC', 'COB_DESC', 'SOURCE_DESC', 'ACC_TYPE_GROUPED'],
    drop_first=True,
    dtype=int
)

print("Encoded shape:", severity_encoded.shape)
print("Columns:", severity_encoded.columns.tolist())

from sklearn.model_selection import train_test_split

# Target = what we're predicting
y = severity_encoded['TOTAL_PAID']

# Features = everything except the target
X = severity_encoded.drop('TOTAL_PAID', axis=1)

# Split: 80% train, 20% test
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

print(f"Training set: {len(X_train)} claims")
print(f"Test set:     {len(X_test)} claims")

import statsmodels.api as sm

# Add a constant (intercept) to the features
# Without this, the model is forced through zero — unrealistic
X_train_const = sm.add_constant(X_train)
X_test_const = sm.add_constant(X_test)

# Build the Gamma GLM with log link
gamma_model = sm.GLM(
    y_train,                                    # target: claim cost
    X_train_const,                              # features
    family=sm.families.Gamma(link=sm.families.links.Log())  # Gamma distribution, log link
)

# Fit the model (this is where it learns the coefficients)
gamma_result = gamma_model.fit()

# Print the full summary
print(gamma_result.summary())


# Extract coefficients into a readable table
coef_table = pd.DataFrame({
    'coefficient': gamma_result.params,
    'std_error': gamma_result.bse,
    'p_value': gamma_result.pvalues
})

# Sort by absolute effect size
coef_table['abs_effect'] = coef_table['coefficient'].abs()
coef_table = coef_table.sort_values('abs_effect', ascending=False)

print("\nCoefficient Table (sorted by effect size):")
print(coef_table.round(4))



# Convert log-coefficients to multipliers
coef_table['multiplier'] = np.exp(coef_table['coefficient'])

print("\nMultipliers (how much each factor changes expected cost):")
for name, row in coef_table.iterrows():
    if name == 'const':
        print(f"  Baseline cost: {row['multiplier']:,.0f} SAR")
    elif row['p_value'] < 0.05:  # only show statistically significant ones
        direction = "increases" if row['multiplier'] > 1 else "decreases"
        pct_change = abs(row['multiplier'] - 1) * 100
        print(f"  {name}: {direction} cost by {pct_change:.1f}% (multiplier: {row['multiplier']:.3f})")
    else:
        print(f"  {name}: NOT statistically significant (p={row['p_value']:.3f})")


# Predict on test set
y_pred = gamma_result.predict(X_test_const)

# Compare actual vs predicted
results = pd.DataFrame({
    'actual': y_test.values,
    'predicted': y_pred.values
})

print("\nPrediction Quality:")
print(f"  Actual mean:    {results['actual'].mean():,.0f} SAR")
print(f"  Predicted mean: {results['predicted'].mean():,.0f} SAR")
print(f"  Actual median:    {results['actual'].median():,.0f} SAR")
print(f"  Predicted median: {results['predicted'].median():,.0f} SAR")


from sklearn.metrics import mean_absolute_error, mean_squared_error

mae = mean_absolute_error(results['actual'], results['predicted'])
rmse = np.sqrt(mean_squared_error(results['actual'], results['predicted']))

print(f"\n  MAE  (avg error): {mae:,.0f} SAR")
print(f"  RMSE (penalizes big errors): {rmse:,.0f} SAR")

# Loss ratio check: does total predicted ≈ total actual?
total_actual = results['actual'].sum()
total_predicted = results['predicted'].sum()
ratio = total_predicted / total_actual
print(f"\n  Total Actual:    {total_actual:,.0f} SAR")
print(f"  Total Predicted: {total_predicted:,.0f} SAR")
print(f"  Ratio: {ratio:.3f}  (1.0 = perfect)")


import matplotlib.pyplot as plt

fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# Left: scatter plot
axes[0].scatter(results['actual'], results['predicted'], alpha=0.3, s=10)
max_val = max(results['actual'].max(), results['predicted'].max())
axes[0].plot([0, max_val], [0, max_val], 'r--', label='Perfect prediction')
axes[0].set_xlabel('Actual Cost (SAR)')
axes[0].set_ylabel('Predicted Cost (SAR)')
axes[0].set_title('Actual vs Predicted (Severity)')
axes[0].legend()

# Right: same but log scale (easier to see)
axes[1].scatter(results['actual'], results['predicted'], alpha=0.3, s=10)
axes[1].plot([0, max_val], [0, max_val], 'r--', label='Perfect prediction')
axes[1].set_xlabel('Actual Cost (SAR)')
axes[1].set_ylabel('Predicted Cost (SAR)')
axes[1].set_title('Actual vs Predicted (Log Scale)')
axes[1].set_xscale('log')
axes[1].set_yscale('log')
axes[1].legend()

plt.tight_layout()
plt.savefig('plots/10_severity_actual_vs_predicted.png', dpi=150)
plt.show()



#Frequency Model
# Load policies and claim counts
policies = pd.read_csv('outputs/policies_cleaned.csv')
claims_bp = pd.read_csv('outputs/claims_by_policy.csv')

# Count total claims per POLICY_NO (ignoring year for now)
claim_counts = claims_bp.groupby('POLICY_NO')['CLAIM_COUNT'].sum().reset_index()
claim_counts.columns = ['POLICY_NO', 'TOTAL_CLAIMS']

# Merge with policies — LEFT join keeps all policies
freq_data = policies.merge(claim_counts, on='POLICY_NO', how='left')

# Policies with no claims → 0
freq_data['TOTAL_CLAIMS'] = freq_data['TOTAL_CLAIMS'].fillna(0).astype(int)

print(f"Total policies: {len(freq_data)}")
print(f"With claims:    {(freq_data['TOTAL_CLAIMS'] > 0).sum()}")
print(f"Without claims: {(freq_data['TOTAL_CLAIMS'] == 0).sum()}")
print(f"\nClaim count distribution:")
print(freq_data['TOTAL_CLAIMS'].value_counts().head(10))

# Keep only motor-related policies
print("Class types:", freq_data['CLASS_DESC'].value_counts().to_dict())

motor_data = freq_data[freq_data['CLASS_DESC'].isin(['Motor Private', 'Motor Commercial', 'Motor Rent a Car'])].copy()
print(f"\nMotor policies only: {len(motor_data)}")
print(f"With claims: {(motor_data['TOTAL_CLAIMS'] > 0).sum()}")

# Features: policy type, class, exposure
freq_features = ['POLICY_TYPE_DESC', 'CLASS_DESC']  # removed SOURCE_DESC

# Encode categoricals
freq_encoded = pd.get_dummies(
    motor_data[freq_features + ['TOTAL_CLAIMS', 'EXPOSURE_YEARS']],
    columns=freq_features,
    drop_first=True,
    dtype=int
)

# Remove any rows with missing exposure
freq_encoded = freq_encoded[freq_encoded['EXPOSURE_YEARS'] > 0].copy()

print("Frequency dataset shape:", freq_encoded.shape)
print("Columns:", freq_encoded.columns.tolist())

y_freq = freq_encoded['TOTAL_CLAIMS']
exposure = freq_encoded['EXPOSURE_YEARS']
X_freq = freq_encoded.drop(['TOTAL_CLAIMS', 'EXPOSURE_YEARS'], axis=1)

X_freq_train, X_freq_test, y_freq_train, y_freq_test, exp_train, exp_test = train_test_split(
    X_freq, y_freq, exposure, test_size=0.2, random_state=42
)

print(f"Train: {len(X_freq_train)} | Test: {len(X_freq_test)}")




#Build the Poisson Frequency Model
# Add constant
X_freq_train_const = sm.add_constant(X_freq_train)
X_freq_test_const = sm.add_constant(X_freq_test)

# The OFFSET is log(exposure) — this makes the model predict a RATE
# (claims per year) instead of a raw count
poisson_model = sm.GLM(
    y_freq_train,
    X_freq_train_const,
    family=sm.families.Poisson(link=sm.families.links.Log()),
    offset=np.log(exp_train)  # ← THIS IS THE EXPOSURE ADJUSTMENT
)

poisson_result = poisson_model.fit()
print(poisson_result.summary())


# Negative Binomial handles overdispersion better
nb_model = sm.GLM(
    y_freq_train,
    X_freq_train_const,
    family=sm.families.NegativeBinomial(alpha=1.0),
    offset=np.log(exp_train)
)

nb_result = nb_model.fit()
print(nb_result.summary())


print("\nModel Comparison:")
print(f"  Poisson AIC:           {poisson_result.aic:,.0f}")
print(f"  Negative Binomial AIC: {nb_result.aic:,.0f}")
print(f"\n  → Lower AIC = better fit")

if nb_result.aic < poisson_result.aic:
    print("  → Negative Binomial wins (as expected with overdispersed data)")
    freq_result = nb_result  # use this one going forward
else:
    print("  → Poisson wins")
    freq_result = poisson_result

freq_coefs = pd.DataFrame({
    'coefficient': freq_result.params,
    'multiplier': np.exp(freq_result.params),
    'p_value': freq_result.pvalues
})

print("\nFrequency Model Coefficients:")
for name, row in freq_coefs.iterrows():
    if name == 'const':
        print(f"  Baseline claim rate: {row['multiplier']:.4f} claims/year")
    else:
        direction = "higher" if row['multiplier'] > 1 else "lower"
        pct = abs(row['multiplier'] - 1) * 100
        sig = "***" if row['p_value'] < 0.01 else "**" if row['p_value'] < 0.05 else "(not significant)"
        print(f"  {name}: {pct:.1f}% {direction} claim rate {sig}")


# For each policy in the test set, compute:
#   Pure Premium = Predicted Frequency × Predicted Severity

# 1. Get severity prediction (average across the test set)
avg_predicted_severity = y_pred.mean()  # from step 2.3
print(f"Average predicted severity: {avg_predicted_severity:,.0f} SAR")

# 2. Get frequency prediction for test policies
freq_pred = freq_result.predict(X_freq_test_const, offset=np.log(exp_test))
print(f"Average predicted frequency: {freq_pred.mean():.4f} claims/year")

# 3. Pure Premium = frequency × severity
pure_premium = freq_pred * avg_predicted_severity
print(f"\nAverage Pure Premium: {pure_premium.mean():,.2f} SAR per policy per year")
print(f"This is what the insurer should charge (before expenses/profit)")


# Save model coefficients
severity_coefs = pd.DataFrame({
    'coefficient': gamma_result.params,
    'multiplier': np.exp(gamma_result.params),
    'p_value': gamma_result.pvalues
})
severity_coefs.to_csv('outputs/severity_model_coefficients.csv')

freq_coefs.to_csv('outputs/frequency_model_coefficients.csv')

print("Saved model coefficients to outputs/")

#-----------------------------------------------------------------------------------------------
# ================================================================
# IMPROVED SEVERITY MODEL
# ================================================================
print("\n" + "=" * 60)
print("IMPROVED SEVERITY MODEL")
print("=" * 60)

# Reload clean severity data (start fresh)
severity_v2 = pd.read_csv('outputs/severity_data.csv')

# Check: what's the 99th percentile?
p99 = severity_v2['TOTAL_PAID'].quantile(0.99)
p95 = severity_v2['TOTAL_PAID'].quantile(0.95)
print(f"95th percentile: {p95:,.0f} SAR")
print(f"99th percentile: {p99:,.0f} SAR")
print(f"Max before cap:  {severity_v2['TOTAL_PAID'].max():,.0f} SAR")

# Cap at 99th percentile
severity_v2['TOTAL_PAID'] = severity_v2['TOTAL_PAID'].clip(upper=p99)
print(f"Max after cap:   {severity_v2['TOTAL_PAID'].max():,.0f} SAR")
print(f"Rows affected:   {(severity_v2['TOTAL_PAID'] == p99).sum()}")


# Group accident types — raise threshold to 40 to absorb Hit and Run
acc_counts_v2 = severity_v2['ACC_TYPE_DESC'].value_counts()
rare_types_v2 = acc_counts_v2[acc_counts_v2 < 40].index.tolist()
severity_v2['ACC_TYPE_GROUPED'] = severity_v2['ACC_TYPE_DESC'].replace(rare_types_v2, 'Other')

print(f"\nAccident type groups (v2):")
print(severity_v2['ACC_TYPE_GROUPED'].value_counts())


# Add claim year as a numeric feature
# Center it so the intercept is easier to interpret
severity_v2['CLM_YEAR_CENTERED'] = severity_v2['CLM_YEAR'] - severity_v2['CLM_YEAR'].median()
print(f"\nCLM_YEAR range: {severity_v2['CLM_YEAR'].min()} to {severity_v2['CLM_YEAR'].max()}")
print(f"After centering: {severity_v2['CLM_YEAR_CENTERED'].min()} to {severity_v2['CLM_YEAR_CENTERED'].max()}")


# Encode features (same process as before, plus CLM_YEAR_CENTERED)
severity_v2_encoded = pd.get_dummies(
    severity_v2[['TOTAL_PAID', 'POLICY_TYPE_DESC', 'COB_DESC', 'SOURCE_DESC',
                 'ACC_TYPE_GROUPED', 'CLM_YEAR_CENTERED']],
    columns=['POLICY_TYPE_DESC', 'COB_DESC', 'SOURCE_DESC', 'ACC_TYPE_GROUPED'],
    drop_first=True,
    dtype=int
)

print(f"\nImproved encoded shape: {severity_v2_encoded.shape}")
print(f"Columns: {severity_v2_encoded.columns.tolist()}")

# Split
y_v2 = severity_v2_encoded['TOTAL_PAID']
X_v2 = severity_v2_encoded.drop('TOTAL_PAID', axis=1)

X_train_v2, X_test_v2, y_train_v2, y_test_v2 = train_test_split(
    X_v2, y_v2, test_size=0.2, random_state=42
)

# Fit improved Gamma GLM
X_train_v2_const = sm.add_constant(X_train_v2)
X_test_v2_const = sm.add_constant(X_test_v2)

gamma_v2 = sm.GLM(
    y_train_v2,
    X_train_v2_const,
    family=sm.families.Gamma(link=sm.families.links.Log())
)

gamma_v2_result = gamma_v2.fit()
print(gamma_v2_result.summary())

# Predictions
y_pred_v2 = gamma_v2_result.predict(X_test_v2_const)

# Compare: old model vs improved model
print("\n" + "=" * 60)
print("COMPARISON: Original vs Improved Severity Model")
print("=" * 60)

mae_v2 = mean_absolute_error(y_test_v2, y_pred_v2)
rmse_v2 = np.sqrt(mean_squared_error(y_test_v2, y_pred_v2))
ratio_v2 = y_pred_v2.sum() / y_test_v2.sum()

print(f"{'Metric':<30} {'Original':>12} {'Improved':>12}")
print(f"{'-'*54}")
print(f"{'MAE (avg error)':<30} {'7,013':>12} {mae_v2:>12,.0f}")
print(f"{'RMSE':<30} {'21,337':>12} {rmse_v2:>12,.0f}")
print(f"{'Total Ratio':<30} {'0.960':>12} {ratio_v2:>12.3f}")
print(f"{'Actual mean':<30} {'8,542':>12} {y_test_v2.mean():>12,.0f}")
print(f"{'Predicted mean':<30} {'8,201':>12} {y_pred_v2.mean():>12,.0f}")


# Multiplier table for the improved model
coef_v2 = pd.DataFrame({
    'coefficient': gamma_v2_result.params,
    'multiplier': np.exp(gamma_v2_result.params),
    'p_value': gamma_v2_result.pvalues
})

print("\nImproved Model — Multipliers:")
for name, row in coef_v2.iterrows():
    if name == 'const':
        print(f"  Baseline cost: {row['multiplier']:,.0f} SAR")
    elif name == 'CLM_YEAR_CENTERED':
        # This one reads differently — it's per-year change
        pct = (row['multiplier'] - 1) * 100
        print(f"  Each additional year: cost changes by {pct:+.1f}% per year (p={row['p_value']:.4f})")
    elif row['p_value'] < 0.05:
        direction = "increases" if row['multiplier'] > 1 else "decreases"
        pct = abs(row['multiplier'] - 1) * 100
        print(f"  {name}: {direction} cost by {pct:.1f}%")
    else:
        print(f"  {name}: NOT significant (p={row['p_value']:.3f})")

coef_v2.to_csv('outputs/severity_model_v2_coefficients.csv')
print("\nSaved: outputs/severity_model_v2_coefficients.csv")

# Add this at the BOTTOM of 03_glm_models.py
scatter_data = pd.DataFrame({
    'Actual': y_test_v2.values,
    'Predicted': y_pred_v2.values
})
scatter_data.to_csv('outputs/dashboard_scatter.csv', index=False)
print("Saved: dashboard_scatter.csv")