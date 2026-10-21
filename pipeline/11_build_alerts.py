"""
11_build_alerts.py - Dynamic Management Alert Generator
=======================================================

Reads existing fact CSVs from the Saudi Motor Insurance DSS and evaluates
a registry of configurable alert rules.

Outputs:
    fact_alerts.csv

Designed for Power BI multi-card / table / matrix visuals.

Each alert is stored as separate business-friendly fields:
    TITLE, ISSUE, IMPACT, ACTION

Usage:
    python pipeline/11_build_alerts.py
    python pipeline/11_build_alerts.py --facts-dir ./02_facts
    python pipeline/11_build_alerts.py --out ./02_facts/fact_alerts.csv
"""

import argparse
import os
from datetime import date
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------

CONFIG = {
    "severity_consecutive_periods": 3,
    "severity_min_pct_change": 0.03,

    "loss_ratio_target": 0.70,
    "loss_ratio_warning_band": 0.05,
    "loss_ratio_critical_threshold": 0.80,

    "settlement_dev_month_check": "12-24",
    "settlement_deviation_threshold": 0.01,

    "pricing_severely_underpriced": 0.80,
    "pricing_underpriced": 0.95,

    "model_bias_threshold": 0.15,
    "model_bias_horizon": "12_months",

    "max_alerts": 12,
    "detected_date": date.today().isoformat(),
}


# ---------------------------------------------------------------------
# TEXT HELPERS
# ---------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Cleans text and guarantees a single sentence ending with a period."""
    text = " ".join(str(text).split())
    if not text.endswith("."):
        text += "."
    return text


def build_title(segment: str, message: str) -> str:
    """
    Standard business-friendly title.

    Examples:
        Riyadh · Private Cars — Claim costs above average
        Portfolio — Claims settling slower than expected
    """
    segment = " ".join(str(segment).split()).strip()
    message = " ".join(str(message).split()).strip()

    if segment in ("", "Portfolio", "All"):
        return message

    return f"{segment} — {message}"


def _join_segment(zone, class_desc, policy_type) -> str:
    parts = []
    for value in (zone, class_desc, policy_type):
        v = str(value).strip()
        if v not in ("", "Portfolio", "All", "nan"):
            parts.append(v)
    return " · ".join(parts) if parts else "Portfolio"


def segment_name(row) -> str:
    """Segment name for a row that uses UPPERCASE keys (source data)."""
    return _join_segment(
        row.get("ZONE", ""),
        row.get("CLASS_DESC", ""),
        row.get("POLICY_TYPE_DESC", ""),
    )


def segment_label_from_alert(row) -> str:
    """Segment name for an alert row that uses lowercase keys."""
    return _join_segment(
        row.get("zone", ""),
        row.get("class_desc", ""),
        row.get("policy_type_desc", ""),
    )


def make_alert(
    rule_id: str,
    severity: str,
    title: str,
    issue: str,
    impact: str,
    action: str,
    row,
    category: str,
    metric_name: str,
    metric_value: float,
    requires_action: bool,
) -> dict:
    """Creates the standard alert structure used by every rule."""
    return {
        "alert_rule_id": rule_id,
        "severity": severity,

        "title": clean_text(title),
        "issue": clean_text(issue),
        "impact": clean_text(impact),
        "action": clean_text(action),

        "zone": row.get("ZONE", "Portfolio"),
        "class_desc": row.get("CLASS_DESC", "All"),
        "policy_type_desc": row.get("POLICY_TYPE_DESC", "All"),

        "category": category,
        "metric_name": metric_name,
        "metric_value": round(metric_value, 4),
        "requires_action": requires_action,
    }


# ---------------------------------------------------------------------
# ALERT RULES
# ---------------------------------------------------------------------

def rule_severity_trend(seg: pd.DataFrame, cfg: dict) -> list:
    """Rule 1: Claim severity above portfolio average."""
    alerts = []

    seg = seg[seg["CLAIM_COUNT"] > 0].copy()
    if seg.empty:
        return alerts

    seg["AVG_SEVERITY"] = seg["ULTIMATE_CLAIMS"] / seg["CLAIM_COUNT"]

    portfolio_severity = seg["ULTIMATE_CLAIMS"].sum() / seg["CLAIM_COUNT"].sum()

    for _, row in seg.iterrows():
        pct_above = (row["AVG_SEVERITY"] - portfolio_severity) / portfolio_severity

        if pct_above >= cfg["severity_min_pct_change"]:
            severity = "critical" if pct_above >= 0.20 else "warning"

            sar_diff = row["AVG_SEVERITY"] - portfolio_severity
            claim_count = int(row["CLAIM_COUNT"])
            extra_cost = sar_diff * claim_count
            segment = segment_name(row)

            issue = (
                f"Average claim cost is SAR {row['AVG_SEVERITY']:,.0f}, "
                f"{pct_above:.0%} above the portfolio average of "
                f"SAR {portfolio_severity:,.0f}"
            )
            impact = (
                f"Across {claim_count:,} claims, this represents "
                f"approximately SAR {extra_cost:,.0f} in additional cost"
            )

            if severity == "critical":
                title_message = "Claim costs significantly above average"
                action = "Review repair channels and route claims to preferred garages"
            else:
                title_message = "Claim costs above average"
                action = "Monitor this segment at the next review"

            alerts.append(
                make_alert(
                    rule_id="severity_trend",
                    severity=severity,
                    title=build_title(segment, title_message),
                    issue=issue,
                    impact=impact,
                    action=action,
                    row=row,
                    category="claims",
                    metric_name="severity_vs_portfolio_pct",
                    metric_value=pct_above,
                    requires_action=(severity == "critical"),
                )
            )

    return alerts


def rule_loss_ratio_target(seg: pd.DataFrame, cfg: dict) -> list:
    """Rule 2: Loss ratio exceeding underwriting target."""
    alerts = []

    seg = seg[seg["EARNED_PREMIUM"] > 0].copy()
    if seg.empty:
        return alerts

    target = cfg["loss_ratio_target"]
    band = cfg["loss_ratio_warning_band"]
    critical = cfg["loss_ratio_critical_threshold"]

    for _, row in seg.iterrows():
        lr = row["LOSS_RATIO"]
        premium = row["EARNED_PREMIUM"]
        claims = row["ULTIMATE_CLAIMS"]
        remaining = max(1 - lr, 0)
        segment = segment_name(row)

        if lr >= critical:
            title_message = "Loss ratio materially above target"
            issue = (
                f"Claims consume {lr:.0%} of earned premium, "
                f"with SAR {claims:,.0f} of claims against "
                f"SAR {premium:,.0f} of earned premium, "
                f"versus the {target:.0%} target"
            )
            impact = (
                f"Only SAR {remaining:.2f} of every SAR 1.00 earned "
                f"remains for expenses and profit"
            )
            action = "Reprice urgently or consider stopping new business in this segment"

            alerts.append(
                make_alert(
                    rule_id="loss_ratio_target",
                    severity="critical",
                    title=build_title(segment, title_message),
                    issue=issue,
                    impact=impact,
                    action=action,
                    row=row,
                    category="profitability",
                    metric_name="loss_ratio",
                    metric_value=lr,
                    requires_action=True,
                )
            )

        elif lr >= (target - band):
            title_message = "Loss ratio approaching target"
            gap_pts = abs(target - lr) * 100

            if lr >= target:
                margin_txt = (
                    f"{gap_pts:.1f} percentage points above the {target:.0%} target"
                )
            else:
                margin_txt = (
                    f"only {gap_pts:.1f} percentage points below the {target:.0%} target"
                )

            issue = f"Claims consume {lr:.0%} of premium, {margin_txt}"
            impact = "A small increase in claims could push this segment past target"
            action = "Monitor closely and review pricing at the next renewal"

            alerts.append(
                make_alert(
                    rule_id="loss_ratio_target",
                    severity="warning",
                    title=build_title(segment, title_message),
                    issue=issue,
                    impact=impact,
                    action=action,
                    row=row,
                    category="profitability",
                    metric_name="loss_ratio",
                    metric_value=lr,
                    requires_action=False,
                )
            )

    return alerts


def rule_safe_zone_breach(seg: pd.DataFrame, cfg: dict) -> list:
    """Rule 3: Segments in Loss or Red zone."""
    alerts = []

    for _, row in seg.iterrows():
        status = str(row.get("SAFE_ZONE_STATUS", "")).strip()

        cr = row["COMBINED_RATIO"]
        premium = row["EARNED_PREMIUM"]
        breakeven = row["BREAK_EVEN_TOTAL"]
        segment = segment_name(row)

        if status == "Loss":
            shortfall = premium - breakeven

            issue = (
                f"Premium is SAR {premium:,.0f}, below the "
                f"SAR {breakeven:,.0f} needed to break even"
            )
            impact = (
                f"The segment has a SAR {abs(shortfall):,.0f} shortfall, "
                f"and additional business increases the loss"
            )
            action = "Reprice immediately or stop writing new business in this segment"

            alerts.append(
                make_alert(
                    rule_id="safe_zone_breach",
                    severity="critical",
                    title=build_title(segment, "Segment is losing money"),
                    issue=issue,
                    impact=impact,
                    action=action,
                    row=row,
                    category="pricing",
                    metric_name="combined_ratio",
                    metric_value=cr,
                    requires_action=True,
                )
            )

        elif status == "Red":
            buffer = max(premium - breakeven, 0)

            issue = (
                f"Premium of SAR {premium:,.0f} is only "
                f"SAR {buffer:,.0f} above the SAR {breakeven:,.0f} "
                f"break-even level"
            )
            impact = "Any discount or small claims increase could push the segment into a loss"
            action = "Require senior approval before reducing rates"

            alerts.append(
                make_alert(
                    rule_id="safe_zone_breach",
                    severity="warning",
                    title=build_title(segment, "Pricing room is very limited"),
                    issue=issue,
                    impact=impact,
                    action=action,
                    row=row,
                    category="pricing",
                    metric_name="combined_ratio",
                    metric_value=cr,
                    requires_action=False,
                )
            )

    return alerts


def rule_settlement_speed(dev_factors: pd.DataFrame, cfg: dict) -> list:
    """Rule 4: Claims taking longer to settle than expected."""
    alerts = []

    target_transition = cfg["settlement_dev_month_check"]

    row = dev_factors[dev_factors["transition"] == target_transition]
    if row.empty:
        return alerts

    factor = row.iloc[0]["factor"]
    threshold = cfg["settlement_deviation_threshold"]
    deviation = factor - 1.0

    if deviation > threshold:
        issue = (
            f"Claims at the {target_transition} month mark "
            f"are settling {deviation:.1%} slower than the "
            f"historical pattern"
        )
        impact = "Slower closure increases uncertainty in claims reserves"
        action = "Review the open claims pipeline with the claims team"

        alerts.append(
            make_alert(
                rule_id="settlement_speed",
                severity="info",
                title="Portfolio — Claims settling slower than expected",
                issue=issue,
                impact=impact,
                action=action,
                row={
                    "ZONE": "Portfolio",
                    "CLASS_DESC": "All",
                    "POLICY_TYPE_DESC": "All",
                },
                category="reserving",
                metric_name="dev_factor_deviation",
                metric_value=deviation,
                requires_action=False,
            )
        )

    return alerts


def rule_pricing_adequacy(pricing: pd.DataFrame, cfg: dict) -> list:
    """Rule 5: Segments where we charge less than the modelled risk cost."""
    alerts = []

    p = pricing[
        (pricing["IS_CANCELLED"] == False)  # noqa: E712
        & (pricing["charged_premium"] > 0)
        & (pricing["pure_premium_period"] > 0)
    ].copy()

    if p.empty:
        return alerts

    seg_agg = (
        p.groupby(["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"])
        .agg(
            total_charged=("charged_premium", "sum"),
            total_pure=("pure_premium_period", "sum"),
            policy_count=("POLICY_NO", "count"),
        )
        .reset_index()
    )

    seg_agg["adequacy_ratio"] = seg_agg["total_charged"] / seg_agg["total_pure"]

    severe = cfg["pricing_severely_underpriced"]
    warn = cfg["pricing_underpriced"]

    for _, row in seg_agg.iterrows():
        ar = row["adequacy_ratio"]
        gap_sar = row["total_pure"] - row["total_charged"]
        policies = int(row["policy_count"])
        segment = segment_name(row)

        if ar < severe:
            issue = (
                f"Across {policies:,} policies, premium of "
                f"SAR {row['total_charged']:,.0f} covers only "
                f"{ar:.0%} of the modelled risk cost of "
                f"SAR {row['total_pure']:,.0f}"
            )
            impact = (
                f"There is an estimated SAR {gap_sar:,.0f} "
                f"shortfall between price and risk cost"
            )
            action = "Raise rates at renewal or tighten underwriting criteria"

            alerts.append(
                make_alert(
                    rule_id="pricing_adequacy",
                    severity="critical",
                    title=build_title(segment, "Pricing is materially below risk cost"),
                    issue=issue,
                    impact=impact,
                    action=action,
                    row=row,
                    category="pricing",
                    metric_name="pricing_adequacy_ratio",
                    metric_value=ar,
                    requires_action=True,
                )
            )

        elif ar < warn:
            issue = (
                f"Across {policies:,} policies, premium of "
                f"SAR {row['total_charged']:,.0f} covers "
                f"{ar:.0%} of the modelled risk cost of "
                f"SAR {row['total_pure']:,.0f}"
            )
            impact = (
                f"There is an estimated SAR {gap_sar:,.0f} "
                f"shortfall between price and risk cost"
            )
            action = "Consider a rate adjustment at renewal"

            alerts.append(
                make_alert(
                    rule_id="pricing_adequacy",
                    severity="warning",
                    title=build_title(segment, "Pricing is slightly below risk cost"),
                    issue=issue,
                    impact=impact,
                    action=action,
                    row=row,
                    category="pricing",
                    metric_name="pricing_adequacy_ratio",
                    metric_value=ar,
                    requires_action=False,
                )
            )

    return alerts


def rule_model_bias(perf: pd.DataFrame, cfg: dict) -> list:
    """Rule 6: Forecast model consistently over- or under-predicting."""
    alerts = []

    horizon = cfg["model_bias_horizon"]
    threshold = cfg["model_bias_threshold"]

    glm = perf[
        (perf["model"] == "glm") & (perf["horizon_label"] == horizon)
    ].copy()

    if glm.empty:
        return alerts

    total_actual = glm["actual"].sum()
    total_signed_error = glm["signed_error"].sum()

    if total_actual == 0:
        return alerts

    bias = total_signed_error / total_actual

    if abs(bias) > threshold:
        if bias < 0:
            issue = (
                f"The forecast model has under-estimated claim costs "
                f"by {abs(bias):.0%} over the past year"
            )
            impact = "Actual claims may exceed the forecast used in pricing and planning"
            action = "Build an appropriate buffer into pricing decisions"
        else:
            issue = (
                f"The forecast model has over-estimated claim costs "
                f"by {abs(bias):.0%} over the past year"
            )
            impact = (
                "Pricing decisions may be based on higher expected "
                "claims than observed"
            )
            action = "Validate model results before using them to reduce rates"

        alerts.append(
            make_alert(
                rule_id="model_bias",
                severity="warning",
                title=build_title("Portfolio", "Forecast model shows material bias"),
                issue=issue,
                impact=impact,
                action=action,
                row={
                    "ZONE": "Portfolio",
                    "CLASS_DESC": "All",
                    "POLICY_TYPE_DESC": "All",
                },
                category="model",
                metric_name="forecast_bias",
                metric_value=bias,
                requires_action=False,
            )
        )

    return alerts


# ---------------------------------------------------------------------
# ALERT RULE REGISTRY
# ---------------------------------------------------------------------

ALERT_RULES = [
    {"id": "severity_trend", "fn": rule_severity_trend, "needs": ["segment"]},
    {"id": "loss_ratio_target", "fn": rule_loss_ratio_target, "needs": ["segment"]},
    {"id": "safe_zone_breach", "fn": rule_safe_zone_breach, "needs": ["segment"]},
    {"id": "settlement_speed", "fn": rule_settlement_speed, "needs": ["dev_factors"]},
    {"id": "pricing_adequacy", "fn": rule_pricing_adequacy, "needs": ["pricing"]},
    {"id": "model_bias", "fn": rule_model_bias, "needs": ["model_perf"]},
]


# ---------------------------------------------------------------------
# SEVERITY RANKING & SORTING
# ---------------------------------------------------------------------

SEVERITY_RANK = {"critical": 1, "warning": 2, "info": 3}


def assign_ranks(alerts: list) -> pd.DataFrame:
    if not alerts:
        return pd.DataFrame()

    df = pd.DataFrame(alerts)

    df["severity_rank"] = df["severity"].map(SEVERITY_RANK).fillna(9)
    df["abs_metric"] = df["metric_value"].abs()

    df = df.sort_values(
        ["severity_rank", "abs_metric"],
        ascending=[True, False],
    ).reset_index(drop=True)

    df["ALERT_ID"] = range(1, len(df) + 1)

    # FIX: alert rows use lowercase keys (zone, class_desc, policy_type_desc)
    df["SEGMENT_LABEL"] = df.apply(segment_label_from_alert, axis=1)

    return df


# ---------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------

OUTPUT_COLUMNS = [
    "ALERT_ID",
    "SEVERITY",
    "SEVERITY_RANK",
    "TITLE",
    "ISSUE",
    "IMPACT",
    "ACTION",
    "ZONE",
    "CLASS_DESC",
    "POLICY_TYPE_DESC",
    "SEGMENT_LABEL",
    "CATEGORY",
    "METRIC_NAME",
    "METRIC_VALUE",
    "REQUIRES_ACTION",
    "DETECTED_DATE",
    "ALERT_RULE_ID",
]


def write_alerts(df: pd.DataFrame, out_path: str, cfg: dict):
    if df.empty:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(
            out_path, index=False, encoding="utf-8"
        )
        print(f"  0 alerts written to {out_path}")
        return

    rename_map = {
        "severity": "SEVERITY",
        "severity_rank": "SEVERITY_RANK",
        "title": "TITLE",
        "issue": "ISSUE",
        "impact": "IMPACT",
        "action": "ACTION",
        "zone": "ZONE",
        "class_desc": "CLASS_DESC",
        "policy_type_desc": "POLICY_TYPE_DESC",
        "category": "CATEGORY",
        "metric_name": "METRIC_NAME",
        "metric_value": "METRIC_VALUE",
        "requires_action": "REQUIRES_ACTION",
        "alert_rule_id": "ALERT_RULE_ID",
    }

    df = df.rename(columns=rename_map)
    df["DETECTED_DATE"] = cfg["detected_date"]

    # Keep only the requested number of alerts, then re-number.
    df = df.head(cfg["max_alerts"]).copy()
    df["ALERT_ID"] = range(1, len(df) + 1)

    df = df[OUTPUT_COLUMNS]
    df.to_csv(out_path, index=False, encoding="utf-8")

    print(f"  {len(df)} alerts written to {out_path}")
    return df


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build management alerts")
    parser.add_argument(
        "--facts-dir",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SYNTHETIC_DSS_DATA", "02_facts"),
        help="Path to the 02_facts directory",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path for fact_alerts.csv",
    )
    args = parser.parse_args()

    facts = Path(args.facts_dir)
    out_path = args.out or str(facts / "fact_alerts.csv")

    print("=" * 60)
    print("Management Alert Generator")
    print("=" * 60)

    data = {}

    load_map = {
        "segment": "fact_segment_performance.csv",
        "dev_factors": "fact_dev_factors_paid.csv",
        "pricing": "fact_policy_pricing.csv",
        "model_perf": "fact_model_performance.csv",
    }

    for key, filename in load_map.items():
        path = facts / filename
        if path.exists():
            data[key] = pd.read_csv(path, encoding="utf-8")
            print(f"  Loaded {filename}: {len(data[key])} rows")
        else:
            print(f"  WARNING: {filename} not found - rules needing it will be skipped")
            data[key] = pd.DataFrame()

    # -----------------------------------------------------------------
    # RUN RULES
    # -----------------------------------------------------------------

    all_alerts = []

    for rule in ALERT_RULES:
        missing = [
            n for n in rule["needs"]
            if data.get(n) is None or data[n].empty
        ]

        if missing:
            print(f"  SKIP rule '{rule['id']}': missing data {missing}")
            continue

        try:
            # Every rule takes (its single dataframe, CONFIG)
            result = rule["fn"](data[rule["needs"][0]], CONFIG)
            print(f"  Rule '{rule['id']}': {len(result)} alerts")
            all_alerts.extend(result)
        except Exception as e:
            print(f"  ERROR in rule '{rule['id']}': {e}")

    # -----------------------------------------------------------------
    # RANK & WRITE
    # -----------------------------------------------------------------

    print(f"\n  Total raw alerts: {len(all_alerts)}")

    df = assign_ranks(all_alerts)
    final = write_alerts(df, out_path, CONFIG)

    # -----------------------------------------------------------------
    # SUMMARY (based on what was actually written)
    # -----------------------------------------------------------------

    if final is not None and not final.empty:
        n_critical = (final["SEVERITY"] == "critical").sum()
        n_warning = (final["SEVERITY"] == "warning").sum()
        n_info = (final["SEVERITY"] == "info").sum()
        n_action = final["REQUIRES_ACTION"].sum()

        print(
            f"\n  Summary: {n_critical} critical, {n_warning} warning, "
            f"{n_info} info - {n_action} require action"
        )

    print("\n" + "=" * 60)
    print("Done.")


if __name__ == "__main__":
    main()