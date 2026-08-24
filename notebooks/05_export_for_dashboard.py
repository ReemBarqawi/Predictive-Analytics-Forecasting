import pandas as pd
import numpy as np

# ── Load everything ──
claims = pd.read_csv('outputs/claims_cleaned.csv', parse_dates=['ACC_DATE'])
severity = pd.read_csv('outputs/severity_data.csv')
policies = pd.read_csv('outputs/policies_cleaned.csv')
claims_bp = pd.read_csv('outputs/claims_by_policy.csv')

# ================================================================
# EXPORT 1: KPI Summary
# ================================================================
kpi = pd.DataFrame([{
    'Total Claims': len(claims),
    'Total Policies': len(policies),
    'Unique Policies with Claims': claims['POLICY_NO'].nunique(),
    'Total Paid (SAR)': claims['TOTAL_PAID'].sum(),
    'Average Claim Cost (SAR)': severity['TOTAL_PAID'].mean(),
    'Median Claim Cost (SAR)': severity['TOTAL_PAID'].median(),
    'Overall Loss Ratio': claims['TOTAL_PAID'].sum() / policies['TOTAL_PREMIUM'].sum(),
    'Pure Premium (SAR)': 741.18,
    'Claim Inflation Rate': 0.045,
    'Model Total Ratio': 1.034,
    'Gini Coefficient': 0.234,
}])
kpi.to_csv('outputs/dashboard_kpis.csv', index=False)
print("Saved: dashboard_kpis.csv")

# ================================================================
# EXPORT 2: Severity Model Multipliers
# ================================================================
multipliers = pd.DataFrame([
    {'Risk Factor': 'Baseline (Comp + Motor Commercial + Broker)', 'Multiplier': 1.0,
     'Effect': 'Reference', 'Cost Impact (SAR)': 17615},
    {'Risk Factor': 'TOTAL LOSS', 'Multiplier': 6.50,
     'Effect': '+550%', 'Cost Impact (SAR)': 17615 * 6.50},
    {'Risk Factor': 'Other Accident Types', 'Multiplier': 2.46,
     'Effect': '+146%', 'Cost Impact (SAR)': 17615 * 2.46},
    {'Risk Factor': 'Motor Trade', 'Multiplier': 0.26,
     'Effect': '-74%', 'Cost Impact (SAR)': 17615 * 0.26},
    {'Risk Factor': 'Motor Private', 'Multiplier': 0.57,
     'Effect': '-43%', 'Cost Impact (SAR)': 17615 * 0.57},
    {'Risk Factor': 'Third Party Policy', 'Multiplier': 0.67,
     'Effect': '-33%', 'Cost Impact (SAR)': 17615 * 0.67},
    {'Risk Factor': '+1 Year (Inflation)', 'Multiplier': 1.045,
     'Effect': '+4.5%/yr', 'Cost Impact (SAR)': 17615 * 1.045},
])
multipliers.to_csv('outputs/dashboard_multipliers.csv', index=False)
print("Saved: dashboard_multipliers.csv")

# ================================================================
# EXPORT 3: Claims Trend by Year
# ================================================================
yearly = claims.groupby('CLM_YEAR').agg(
    Claim_Count=('CLM_NO', 'count'),
    Total_Paid=('TOTAL_PAID', 'sum'),
    Avg_Cost=('TOTAL_PAID', 'mean'),
    Median_Cost=('TOTAL_PAID', 'median'),
).reset_index()
yearly.columns = ['Year', 'Claim Count', 'Total Paid (SAR)', 'Avg Cost (SAR)', 'Median Cost (SAR)']
yearly.to_csv('outputs/dashboard_yearly_trend.csv', index=False)
print("Saved: dashboard_yearly_trend.csv")

# ================================================================
# EXPORT 4: Claims by Accident Type
# ================================================================
acc_summary = claims[claims['TOTAL_PAID'] > 0].groupby('ACC_TYPE_DESC').agg(
    Count=('CLM_NO', 'count'),
    Total_Paid=('TOTAL_PAID', 'sum'),
    Avg_Cost=('TOTAL_PAID', 'mean'),
    Median_Cost=('TOTAL_PAID', 'median'),
).reset_index()
acc_summary.columns = ['Accident Type', 'Count', 'Total Paid (SAR)', 'Avg Cost (SAR)', 'Median Cost (SAR)']
acc_summary = acc_summary.sort_values('Total Paid (SAR)', ascending=False)
acc_summary.to_csv('outputs/dashboard_accident_types.csv', index=False)
print("Saved: dashboard_accident_types.csv")

# ================================================================
# EXPORT 5: Policy Type x COB Breakdown
# ================================================================
seg = claims[claims['TOTAL_PAID'] > 0].groupby(['POLICY_TYPE_DESC', 'COB_DESC']).agg(
    Claim_Count=('CLM_NO', 'count'),
    Total_Paid=('TOTAL_PAID', 'sum'),
    Avg_Cost=('TOTAL_PAID', 'mean'),
).reset_index()
seg.columns = ['Policy Type', 'Class of Business', 'Claim Count', 'Total Paid (SAR)', 'Avg Cost (SAR)']
seg.to_csv('outputs/dashboard_segments.csv', index=False)
print("Saved: dashboard_segments.csv")

# ================================================================
# EXPORT 6: Lift Chart Data
# Replace with YOUR actual numbers from the evaluation output
# ================================================================
lift_data = pd.DataFrame({
    'Decile': [1, 2, 3, 4, 5, 6, 7, 8],
    'Avg Actual (SAR)': [3900, 5600, 6500, 14500, 4800, 8800, 9100, 21000],
    'Avg Predicted (SAR)': [3500, 5900, 6200, 6300, 6500, 9000, 10500, 20000],
    'Risk Level': ['Very Low', 'Low', 'Low-Medium', 'Medium', 'Medium',
                   'Medium-High', 'High', 'Very High']
})
lift_data.to_csv('outputs/dashboard_lift_chart.csv', index=False)
print("Saved: dashboard_lift_chart.csv")

# ================================================================
# EXPORT 7: Premium by Policy Type
# ================================================================
portfolio = policies.groupby('POLICY_TYPE_DESC').agg(
    Policy_Count=('POLICY_NO', 'count'),
    Total_Premium=('TOTAL_PREMIUM', 'sum'),
    Avg_Premium=('TOTAL_PREMIUM', 'mean'),
).reset_index()
portfolio.columns = ['Policy Type', 'Policy Count', 'Total Premium (SAR)', 'Avg Premium (SAR)']
portfolio.to_csv('outputs/dashboard_portfolio.csv', index=False)
print("Saved: dashboard_portfolio.csv")

# ================================================================
# EXPORT 8: Actuals vs Forecast Scenarios
# Based on the 4.5% inflation rate the model discovered
# 3 scenarios: Optimistic (2%), Baseline (4.5%), Pessimistic (7%)
# ================================================================
yearly_full = claims.groupby('CLM_YEAR').agg(
    Total_Paid=('TOTAL_PAID', 'sum'),
    Avg_Cost=('TOTAL_PAID', 'mean'),
    Claim_Count=('CLM_NO', 'count')
).reset_index()
yearly_full.columns = ['Year', 'Total_Paid', 'Avg_Cost', 'Claim_Count']

# Use only years with enough data (50+ claims)
yearly_reliable = yearly_full[yearly_full['Claim_Count'] >= 50].copy()
last_year = yearly_reliable['Year'].max()
last_value = yearly_reliable[yearly_reliable['Year'] == last_year]['Total_Paid'].values[0]
last_avg = yearly_reliable[yearly_reliable['Year'] == last_year]['Avg_Cost'].values[0]

print(f"\nForecast base: Year {last_year}, Total = {last_value:,.0f}, Avg = {last_avg:,.0f}")

# Build 3-year forecast
scenarios = {
    'Optimistic': 0.02,
    'Baseline': 0.045,
    'Pessimistic': 0.07
}

forecast_rows = []
for year_offset in range(1, 4):
    year = last_year + year_offset
    for name, rate in scenarios.items():
        forecast_rows.append({
            'Year': year,
            'Scenario': name,
            'Forecast_Total_Paid': last_value * ((1 + rate) ** year_offset),
            'Forecast_Avg_Cost': last_avg * ((1 + rate) ** year_offset),
            'Growth_Rate': f"+{rate*100:.1f}%/yr"
        })

forecasts = pd.DataFrame(forecast_rows)

# Wide format (one column per scenario — for the line chart)
actuals_for_chart = yearly_reliable[['Year', 'Total_Paid', 'Avg_Cost']].copy()
actuals_for_chart = actuals_for_chart.rename(columns={
    'Total_Paid': 'Actuals_Total', 'Avg_Cost': 'Actuals_Avg'
})

forecast_wide = forecasts.pivot(
    index='Year', columns='Scenario', values='Forecast_Total_Paid'
).reset_index()
forecast_wide.columns = ['Year', 'Scenario_Baseline', 'Scenario_Optimistic', 'Scenario_Pessimistic']

scenario_chart = actuals_for_chart.merge(forecast_wide, on='Year', how='outer')
scenario_chart = scenario_chart.sort_values('Year')
scenario_chart.to_csv('outputs/dashboard_scenarios_wide.csv', index=False)
print("Saved: dashboard_scenarios_wide.csv")

# Long format (for slicer approach)
actuals_long = yearly_reliable[['Year', 'Total_Paid']].copy()
actuals_long['Scenario'] = 'Actuals'
actuals_long = actuals_long.rename(columns={'Total_Paid': 'Amount'})

forecasts_long = forecasts[['Year', 'Scenario', 'Forecast_Total_Paid']].copy()
forecasts_long = forecasts_long.rename(columns={'Forecast_Total_Paid': 'Amount'})

combined_long = pd.concat([actuals_long, forecasts_long], ignore_index=True)
combined_long = combined_long.sort_values(['Year', 'Scenario'])
combined_long.to_csv('outputs/dashboard_scenarios_long.csv', index=False)
print("Saved: dashboard_scenarios_long.csv")

# Scenario summary for cards
scenario_summary = []
for name, rate in scenarios.items():
    projected_3yr = last_value * ((1 + rate) ** 3)
    diff = projected_3yr - last_value
    pct = ((projected_3yr / last_value) - 1) * 100
    scenario_summary.append({
        'Scenario': name,
        'Growth Rate': f"+{rate*100:.1f}%",
        'Year 1 Projection': last_value * (1 + rate),
        'Year 2 Projection': last_value * ((1 + rate) ** 2),
        'Year 3 Projection': projected_3yr,
        'Total 3yr Change (SAR)': diff,
        'Total 3yr Change (%)': pct,
        'Base Year': last_year,
        'Base Value (SAR)': last_value,
    })

pd.DataFrame(scenario_summary).to_csv('outputs/dashboard_scenario_summary.csv', index=False)
print("Saved: dashboard_scenario_summary.csv")


scatter_data = pd.DataFrame({
    'Actual': y_test.values,
    'Predicted': y_pred.values
})
scatter_data.to_csv('outputs/dashboard_scatter.csv', index=False)

print("\n" + "=" * 50)
print("All 10 dashboard files exported to outputs/")
print("=" * 50)