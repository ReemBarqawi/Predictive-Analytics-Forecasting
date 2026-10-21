import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# Use a clean style
plt.style.use('seaborn-v0_8-whitegrid')

# Load your cleaned data
claims = pd.read_csv('outputs/claims_cleaned.csv')
severity = pd.read_csv('outputs/severity_data.csv')
claims_by_policy = pd.read_csv('outputs/claims_by_policy.csv')
policies = pd.read_csv('outputs/policies_cleaned.csv')


fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# Left: raw distribution
axes[0].hist(severity['TOTAL_PAID'], bins=100, color='orange', edgecolor='white')
axes[0].set_xlabel('Claim Cost (SAR)')
axes[0].set_ylabel('Number of Claims')
axes[0].set_title('Severity — Raw (notice the long right tail)')


#Chart 1: Severity Distribution
# Add mean and median lines to SEE the skew
axes[0].axvline(severity['TOTAL_PAID'].median(), color='red', linestyle='--', 
                label=f"Median: {severity['TOTAL_PAID'].median():,.0f}")
axes[0].axvline(severity['TOTAL_PAID'].mean(), color='darkred', linestyle='-',
                label=f"Mean: {severity['TOTAL_PAID'].mean():,.0f}")
axes[0].legend()

# Right: log-transformed (should look more bell-shaped)
axes[1].hist(np.log1p(severity['TOTAL_PAID']), bins=50, color='purple', edgecolor='white')
axes[1].set_xlabel('log(Claim Cost)')
axes[1].set_title('Severity — Log Scale (more symmetric = confirms Gamma)')

plt.tight_layout()
plt.savefig('plots/01_severity_distribution.png', dpi=150)
plt.show()  

#Chart 2: Claim Frequency
fig, ax = plt.subplots(figsize=(8, 5))

freq = claims_by_policy['CLAIM_COUNT'].clip(upper=10)  # cap at 10 for readability
ax.hist(freq, bins=range(1, 12), color='steelblue', edgecolor='white', align='left')
ax.set_xlabel('Number of Claims per Policy')
ax.set_ylabel('Number of Policies')
ax.set_title('How Many Claims Did Each Policy Have?')
ax.set_xticks(range(1, 11))
ax.set_xticklabels([str(i) for i in range(1, 10)] + ['10+'])

plt.tight_layout()
plt.savefig('plots/02_claim_frequency.png', dpi=150)
plt.show()


#Chart 3: Accident Types
fig, axes = plt.subplots(1, 2, figsize=(16, 6))

# Left: most common
acc_counts = claims['ACC_TYPE_DESC'].value_counts().head(8)
axes[0].barh(acc_counts.index[::-1], acc_counts.values[::-1], color='steelblue')
axes[0].set_xlabel('Number of Claims')
axes[0].set_title('Most Common Accident Types')

# Right: most expensive (average cost)
acc_cost = claims[claims['TOTAL_PAID'] > 0].groupby('ACC_TYPE_DESC')['TOTAL_PAID'].mean()
acc_cost = acc_cost.sort_values().tail(8)
axes[1].barh(acc_cost.index, acc_cost.values, color='tomato')
axes[1].set_xlabel('Average Claim Cost (SAR)')
axes[1].set_title('Most Expensive Accident Types')

plt.tight_layout()
plt.savefig('plots/03_accident_types.png', dpi=150)
plt.show()

#Chart 4: Severity by Policy Type
fig, ax = plt.subplots(figsize=(8, 5))

# Use boxplot — it shows median, spread, and outliers
data_for_plot = claims[claims['TOTAL_PAID'] > 0].copy()
# Cap at 95th percentile so outliers don't squish the chart
cap = data_for_plot['TOTAL_PAID'].quantile(0.95)
data_for_plot['TOTAL_PAID'] = data_for_plot['TOTAL_PAID'].clip(upper=cap)

sns.boxplot(data=data_for_plot, x='POLICY_TYPE_DESC', y='TOTAL_PAID', hue='POLICY_TYPE_DESC')
ax.set_ylabel('Claim Cost (SAR)')
ax.set_title('Claim Severity: Comprehensive vs Third Party')

plt.tight_layout()
plt.savefig('plots/04_severity_by_policy_type.png', dpi=150)
plt.show()

#Chart 5: Claims Over Time
fig, axes = plt.subplots(2, 1, figsize=(12, 8))

yearly = claims.groupby('CLM_YEAR').agg(
    count=('CLM_NO', 'count'),
    total_paid=('TOTAL_PAID', 'sum')
).reset_index()

axes[0].bar(yearly['CLM_YEAR'], yearly['count'], color='steelblue')
axes[0].set_ylabel('Number of Claims')
axes[0].set_title('Claim Count by Year')

axes[1].bar(yearly['CLM_YEAR'], yearly['total_paid'] / 1e6, color='orange')
axes[1].set_ylabel('Total Paid (Millions SAR)')
axes[1].set_title('Total Claims Cost by Year')
axes[1].set_xlabel('Year')

plt.tight_layout()
plt.savefig('plots/05_claims_over_time.png', dpi=150)
plt.show()

mean = claims_by_policy['CLAIM_COUNT'].mean()
var = claims_by_policy['CLAIM_COUNT'].var()
ratio = var / mean

print(f"Mean claim count: {mean:.4f}")
print(f"Variance:         {var:.4f}")
print(f"Ratio (var/mean): {ratio:.2f}")
print()
if ratio > 2:
    print("OVERDISPERSED → Use Negative Binomial (not plain Poisson)")
else:
    print("OK → Poisson is fine")