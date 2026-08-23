import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import statsmodels.api as sm
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, mean_squared_error

plt.style.use('seaborn-v0_8-whitegrid')

# Load and prepare (same as improved version)
severity = pd.read_csv('outputs/severity_data.csv')

# Cap outliers at 99th percentile
p99 = severity['TOTAL_PAID'].quantile(0.99)
severity['TOTAL_PAID'] = severity['TOTAL_PAID'].clip(upper=p99)

# Group rare accident types
acc_counts = severity['ACC_TYPE_DESC'].value_counts()
rare = acc_counts[acc_counts < 40].index.tolist()
severity['ACC_TYPE_GROUPED'] = severity['ACC_TYPE_DESC'].replace(rare, 'Other')

# Add centered year
severity['CLM_YEAR_CENTERED'] = severity['CLM_YEAR'] - severity['CLM_YEAR'].median()

# Encode
encoded = pd.get_dummies(
    severity[['TOTAL_PAID', 'POLICY_TYPE_DESC', 'COB_DESC', 'SOURCE_DESC',
              'ACC_TYPE_GROUPED', 'CLM_YEAR_CENTERED']],
    columns=['POLICY_TYPE_DESC', 'COB_DESC', 'SOURCE_DESC', 'ACC_TYPE_GROUPED'],
    drop_first=True,
    dtype=int
)

y = encoded['TOTAL_PAID']
X = encoded.drop('TOTAL_PAID', axis=1)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

X_train_c = sm.add_constant(X_train)
X_test_c = sm.add_constant(X_test)

model = sm.GLM(y_train, X_train_c, family=sm.families.Gamma(link=sm.families.links.Log()))
result = model.fit()

y_pred = result.predict(X_test_c)
print(f"Model loaded. Test set: {len(y_test)} claims")

# Create a results dataframe
eval_df = pd.DataFrame({
    'actual': y_test.values,
    'predicted': y_pred.values
})

# Sort by predicted cost and split into 10 equal groups (deciles)
eval_df['decile'] = pd.qcut(eval_df['predicted'], q=10, labels=False, duplicates='drop') + 1

# For each decile: average actual vs average predicted
lift = eval_df.groupby('decile').agg(
    avg_actual=('actual', 'mean'),
    avg_predicted=('predicted', 'mean'),
    count=('actual', 'count')
).reset_index()

print("\nLift Table:")
print(f"{'Decile':>7} {'Avg Actual':>12} {'Avg Predicted':>14} {'Count':>7} {'Lift':>7}")
print("-" * 50)

overall_avg = eval_df['actual'].mean()
for _, row in lift.iterrows():
    lift_ratio = row['avg_actual'] / overall_avg
    print(f"{int(row['decile']):>7} {row['avg_actual']:>12,.0f} {row['avg_predicted']:>14,.0f} "
          f"{int(row['count']):>7} {lift_ratio:>7.2f}")

fig, ax = plt.subplots(figsize=(10, 6))

x = lift['decile']
width = 0.35

ax.bar(x - width/2, lift['avg_actual'], width, label='Actual Average', color='steelblue')
ax.bar(x + width/2, lift['avg_predicted'], width, label='Predicted Average', color='tomato')

ax.set_xlabel('Risk Decile (1=lowest, 10=highest)')
ax.set_ylabel('Average Claim Cost (SAR)')
ax.set_title('Lift Chart — Does the Model Separate Risk Correctly?', fontsize=13, fontweight='bold')
ax.set_xticks(range(1, 11))
ax.legend()

# Add a reference line for overall average
ax.axhline(overall_avg, color='gray', linestyle='--', alpha=0.5, label='Overall Average')

plt.tight_layout()
plt.savefig('plots/11_lift_chart.png', dpi=150)
plt.show()

def gini_coefficient(actual, predicted):
    """Calculate the Gini coefficient for model ranking quality."""
    # Combine and sort by predicted (ascending)
    data = pd.DataFrame({'actual': actual, 'predicted': predicted})
    data = data.sort_values('predicted', ascending=False).reset_index(drop=True)
    
    n = len(data)
    total_actual = data['actual'].sum()
    
    # Cumulative share of actual losses
    cumulative_actual = data['actual'].cumsum() / total_actual
    # Cumulative share of population
    cumulative_pop = np.arange(1, n + 1) / n
    
    # Gini = 2 * (area under Lorenz curve - 0.5)
    # Area under curve using trapezoidal rule
    area = np.trapz(cumulative_actual, cumulative_pop)
    gini = 2 * area - 1
    
    return gini, cumulative_pop, cumulative_actual

gini, cum_pop, cum_actual = gini_coefficient(y_test.values, y_pred.values)
print(f"\nGini Coefficient: {gini:.4f}")

if gini > 0.3:
    print("→ Good ranking ability for an insurance GLM")
elif gini > 0.15:
    print("→ Moderate ranking ability — typical with limited features")
else:
    print("→ Weak ranking — model barely outperforms random")


fig, ax = plt.subplots(figsize=(8, 8))

# Perfect equality line (random model)
ax.plot([0, 1], [0, 1], 'k--', label='Random Model (Gini=0)')

# Model's curve
ax.plot(cum_pop, cum_actual, 'b-', linewidth=2,
        label=f'Your Model (Gini={gini:.3f})')

# Fill the area that represents model improvement
ax.fill_between(cum_pop, cum_actual, cum_pop, alpha=0.15, color='blue')

ax.set_xlabel('Cumulative % of Policies (sorted by predicted risk)')
ax.set_ylabel('Cumulative % of Actual Losses')
ax.set_title('Lorenz Curve — Model Ranking Quality', fontsize=13, fontweight='bold')
ax.legend(loc='upper left')
ax.set_xlim(0, 1)
ax.set_ylim(0, 1)

plt.tight_layout()
plt.savefig('plots/12_lorenz_curve.png', dpi=150)
plt.show()

# Rebuild test set with original feature names for segmentation
test_indices = X_test.index
severity_test = severity.iloc[test_indices].copy()
severity_test['predicted'] = y_pred.values
severity_test['actual'] = y_test.values

# By Policy Type
print("\nActual vs Predicted by Policy Type:")
print(f"{'Type':<20} {'Avg Actual':>12} {'Avg Predicted':>14} {'Ratio':>8} {'Count':>7}")
print("-" * 65)
for ptype in severity_test['POLICY_TYPE_DESC'].unique():
    subset = severity_test[severity_test['POLICY_TYPE_DESC'] == ptype]
    ratio = subset['predicted'].mean() / subset['actual'].mean()
    print(f"{ptype:<20} {subset['actual'].mean():>12,.0f} {subset['predicted'].mean():>14,.0f} "
          f"{ratio:>8.3f} {len(subset):>7}")

# By COB
print("\nActual vs Predicted by Class of Business:")
print(f"{'COB':<20} {'Avg Actual':>12} {'Avg Predicted':>14} {'Ratio':>8} {'Count':>7}")
print("-" * 65)
for cob in severity_test['COB_DESC'].unique():
    subset = severity_test[severity_test['COB_DESC'] == cob]
    ratio = subset['predicted'].mean() / subset['actual'].mean()
    print(f"{cob:<20} {subset['actual'].mean():>12,.0f} {subset['predicted'].mean():>14,.0f} "
          f"{ratio:>8.3f} {len(subset):>7}")

fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# Left: scatter plot
axes[0].scatter(severity_test['actual'], severity_test['predicted'],
                alpha=0.3, s=10, color='steelblue')
max_val = max(severity_test['actual'].max(), severity_test['predicted'].max())
axes[0].plot([0, max_val], [0, max_val], 'r--', label='Perfect prediction')
axes[0].set_xlabel('Actual Cost (SAR)')
axes[0].set_ylabel('Predicted Cost (SAR)')
axes[0].set_title('Actual vs Predicted — Improved Model', fontsize=12, fontweight='bold')
axes[0].legend()

# Right: residual plot (shows error patterns)
residuals = severity_test['actual'] - severity_test['predicted']
axes[1].scatter(severity_test['predicted'], residuals, alpha=0.3, s=10, color='tomato')
axes[1].axhline(0, color='black', linestyle='-', linewidth=0.5)
axes[1].set_xlabel('Predicted Cost (SAR)')
axes[1].set_ylabel('Residual (Actual - Predicted)')
axes[1].set_title('Residual Plot — Error Pattern', fontsize=12, fontweight='bold')

plt.tight_layout()
plt.savefig('plots/13_improved_scatter.png', dpi=150)
plt.show()

print("\n" + "=" * 60)
print("FINAL EVALUATION SUMMARY")
print("=" * 60)

mae = mean_absolute_error(severity_test['actual'], severity_test['predicted'])
rmse = np.sqrt(mean_squared_error(severity_test['actual'], severity_test['predicted']))
ratio = severity_test['predicted'].sum() / severity_test['actual'].sum()

print(f"""
Severity Model (Gamma GLM, Improved):
  MAE:          {mae:,.0f} SAR
  RMSE:         {rmse:,.0f} SAR
  Total Ratio:  {ratio:.3f} (1.0 = perfect calibration)
  Gini:         {gini:.4f}

Interpretation:
  - The model captures {ratio*100:.1f}% of actual total losses
  - Gini of {gini:.4f} indicates {'good' if gini > 0.3 else 'moderate' if gini > 0.15 else 'limited'} risk ranking ability
  - Key risk factors: Total Loss (6.5x), Natural Perils (3.9x), Motor Trade (-75%)
""")