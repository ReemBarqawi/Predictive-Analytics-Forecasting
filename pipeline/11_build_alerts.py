"""
11_build_alerts.py - Management alert inputs
============================================

Method: docs/ALERTS_METHOD.md.

Outputs:
    fact_alert_basis.csv  one row per segment (zone x class x policy type) with the facts the
                          segment alerts need: actual results over the latest and prior 12 complete
                          months by accident date, the model's expected cost on the same premium,
                          credibility Z and the trend noise band. No target and no severity:
                          Power BI decides the alerts in DAX from [Target CR].
    fact_alerts.csv       portfolio-level checks that do not depend on the target
                          (claims settlement speed, forecast model bias).

Usage:
    python pipeline/11_build_alerts.py
    python pipeline/11_build_alerts.py --facts-dir ./02_facts
"""

import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from settings import S, year_basis


# ---------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------

SEG = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]
CLASS = ["CLASS_DESC", "POLICY_TYPE_DESC"]

# Approved window length (docs/ALERTS_METHOD.md §9).
WINDOW_MONTHS = 12

# Method constant: a trailing month whose claim frequency (claims per SAR of earned premium) is more
# than this far below the average of the 12 months before it is treated as not fully reported.
LATE_REPORTING_TOLERANCE = 0.30

CONFIG = {
    # Portfolio checks
    "settlement_dev_month_check": "12-24",
    "settlement_deviation_threshold": 0.01,
    "model_bias_threshold": 0.15,
    "model_bias_horizon": "12_months",

    "max_alerts": 12,
    "detected_date": date.today().isoformat(),
}


# ---------------------------------------------------------------------
# SEGMENT ALERT BASIS
# ---------------------------------------------------------------------

def last_reported_month(claims: pd.DataFrame, ep_month: pd.Series) -> pd.Period:
    """Last month of claims data that is complete and fully reported (method §3)."""
    cutoff = claims["ACC_DATE"].max()
    last = cutoff.to_period("M")
    if cutoff < last.to_timestamp(how="end").normalize():
        last -= 1  # the cut-off month is only partly in the data

    counts = claims.groupby(claims["ACC_DATE"].dt.to_period("M")).size()
    freq = (counts / ep_month.reindex(counts.index)).dropna()
    while last in freq.index:
        prior = freq[(freq.index < last) & (freq.index >= last - 12)]
        if prior.empty or freq[last] >= (1 - LATE_REPORTING_TOLERANCE) * prior.mean():
            break
        print(f"  {last}: claim frequency {freq[last] / prior.mean():.0%} of the prior 12 months, "
              f"treated as not fully reported")
        last -= 1
    return last


def window_totals(ep: pd.DataFrame, claims: pd.DataFrame, first: pd.Period, last: pd.Period) -> pd.DataFrame:
    """Earned premium and accident-date claims per segment over [first, last] months."""
    in_ep = (ep["MONTH"] >= first) & (ep["MONTH"] <= last)
    in_clm = (claims["ACC_MONTH"] >= first) & (claims["ACC_MONTH"] <= last)
    e = ep[in_ep].groupby(SEG).EARNED_PREMIUM.sum().rename("EP")
    c = claims[in_clm].assign(SQ=lambda d: d.ULTIMATE_CLAIM ** 2).groupby(SEG).agg(
        CLAIMS=("ULTIMATE_CLAIM", "sum"), CLAIM_COUNT=("CLM_NO", "size"), SUMSQ=("SQ", "sum"))
    return pd.concat([e, c], axis=1).fillna(0)


def earned_share(ep: pd.DataFrame, pp: pd.DataFrame, first: pd.Period, last: pd.Period,
                 term_ep: pd.DataFrame) -> pd.DataFrame:
    """Model expected cost and commission earned over the window. Both are written per policy for the
    whole term, so each segment x policy year contributes in proportion to the premium it earned
    inside the window."""
    full = term_ep.groupby(SEG + ["POLICY_YEAR"]).EARNED_PREMIUM.sum()
    win = ep[(ep["MONTH"] >= first) & (ep["MONTH"] <= last)].groupby(SEG + ["POLICY_YEAR"]).EARNED_PREMIUM.sum()
    term = pp.groupby(SEG + ["POLICY_YEAR"])[["pure_premium_period", "COMMISSION"]].sum()
    share = (win / full.replace(0, np.nan)).rename("share")
    out = term.join(share, how="inner").fillna({"share": 0})
    out = out[["pure_premium_period", "COMMISSION"]].mul(out["share"], axis=0)
    return out.groupby(SEG).sum().rename(columns={"pure_premium_period": "MODEL_CLAIMS",
                                                  "COMMISSION": "COMMISSION"})


def model_claims(ep, pp, claims, history_first, l_first, last, term_ep) -> pd.DataFrame:
    """The GLM's expected cost over the window, re-based to each class's actual level.
    The GLM is not calibrated by class (on 2022-23 policy years it gives 3.6-5.1x actual for Motor
    Commercial Comprehensive and 0.3-0.6x for Motor Commercial Third Party), so it is scaled per
    class by actual / model over the history before the window. What remains is the model's view of
    how each segment differs from its class."""
    hist_last = l_first - 1
    hist_model = earned_share(ep, pp, history_first, hist_last, term_ep).MODEL_CLAIMS
    hist_actual = window_totals(ep, claims, history_first, hist_last).CLAIMS
    h = pd.concat([hist_model.rename("model"), hist_actual.rename("actual")], axis=1).fillna(0)
    h = h.groupby(CLASS).sum()
    factor = (h.actual / h.model.replace(0, np.nan)).rename("CLASS_MODEL_FACTOR")

    win = earned_share(ep, pp, l_first, last, term_ep).reset_index()
    win = win.merge(factor.reset_index(), on=CLASS, how="left")
    win["MODEL_CLAIMS_L12"] = win.MODEL_CLAIMS * win.CLASS_MODEL_FACTOR
    return win.set_index(SEG)[["MODEL_CLAIMS_L12", "COMMISSION", "CLASS_MODEL_FACTOR"]].rename(
        columns={"COMMISSION": "COMMISSION_L12"})


def credibility_z(ep: pd.DataFrame, claims: pd.DataFrame, last: pd.Period, weight_l12: pd.Series) -> tuple[pd.Series, float]:
    """Bühlmann-Straub credibility of each segment's loss ratio against its class (zone blend).
    Observations: the segment's loss ratio relative to its class in the same 12-month block, for
    consecutive blocks ending at the window end, weighted by earned premium. Taking it relative to
    the class removes swings that hit the whole class (claims inflation, market cycle), which
    would otherwise count as segment noise. K = within-segment variance / between-segment variance."""
    blocks = []
    first_month = ep["MONTH"].min()
    end = last
    while end - (WINDOW_MONTHS - 1) >= first_month:
        t = window_totals(ep, claims, end - (WINDOW_MONTHS - 1), end)
        blocks.append(t.assign(BLOCK=str(end)))
        end -= WINDOW_MONTHS
    b = pd.concat(blocks).reset_index()
    b = b[b.EP > 0]
    cls = b.groupby(CLASS + ["BLOCK"])[["CLAIMS", "EP"]].transform("sum")
    b["X"] = (b.CLAIMS / b.EP) / (cls.CLAIMS / cls.EP)
    b = b[np.isfinite(b.X)]

    seg = b.groupby(SEG).apply(lambda g: pd.Series({
        "m": g.EP.sum(), "n": len(g), "xbar": np.average(g.X, weights=g.EP),
        "ss": (g.EP * (g.X - np.average(g.X, weights=g.EP)) ** 2).sum()}), include_groups=False).reset_index()
    multi = seg[seg.n >= 2]
    s2 = multi.ss.sum() / (multi.n - 1).sum()

    a_num, a_den, n_seg = 0.0, 0.0, 0
    for _, g in seg.groupby(CLASS):
        m_c = g.m.sum()
        xc = np.average(g.xbar, weights=g.m)
        a_num += (g.m * (g.xbar - xc) ** 2).sum()
        a_den += m_c - (g.m ** 2).sum() / m_c
        n_seg += len(g) - 1
    a = (a_num - n_seg * s2) / a_den if a_den > 0 else 0.0
    if a <= 0:
        print("  credibility: segments do not differ beyond noise inside their class; Z = 0")
        return pd.Series(0.0, index=weight_l12.index), float("inf")
    k = s2 / a
    print(f"  credibility: K = SAR {k:,.0f} of earned premium "
          f"({len(blocks)} blocks of {WINDOW_MONTHS} months, {len(seg)} segments)")
    return weight_l12 / (weight_l12 + k), k


def build_alert_basis(facts: Path) -> pd.DataFrame:
    ep = pd.read_csv(facts / "fact_earned_premium.csv",
                     usecols=SEG + ["POLICY_YEAR", "MONTH_DATE", "EARNED_PREMIUM"], parse_dates=["MONTH_DATE"])
    ep["MONTH"] = ep["MONTH_DATE"].dt.to_period("M")
    pp = pd.read_csv(facts / "fact_policy_pricing.csv",
                     usecols=SEG + ["POLICY_NO", "POLICY_YEAR", "COMMISSION", "pure_premium_period"])
    fc = pd.read_csv(facts / "fact_claim.csv",
                     usecols=["POLICY_NO", "POLICY_YEAR", "CLM_NO", "ACC_DATE", "ULTIMATE_CLAIM", "POLICY_MATCHED"],
                     parse_dates=["ACC_DATE"])

    matched = fc[fc["POLICY_MATCHED"].astype(str).str.lower() == "true"]
    keys = pp[["POLICY_NO", "POLICY_YEAR"] + SEG].drop_duplicates(["POLICY_NO", "POLICY_YEAR"])
    claims = matched.merge(keys, on=["POLICY_NO", "POLICY_YEAR"], how="inner").dropna(subset=["ACC_DATE"])
    claims["ACC_MONTH"] = claims["ACC_DATE"].dt.to_period("M")

    term_ep = ep
    if year_basis() == "written_year":
        # Alerts judge actual results, so the same policies are followed only as far as they have run:
        # each year's policies, earned and claimed inside that year. The year's continuing premium has
        # no claims yet, and comparing year against year stays like for like.
        ep = ep[ep["MONTH"].dt.year == ep["POLICY_YEAR"]]
        claims = claims[claims["ACC_MONTH"].dt.year == claims["POLICY_YEAR"]]

    last = last_reported_month(claims, ep.groupby("MONTH").EARNED_PREMIUM.sum())
    l_first, p_last = last - (WINDOW_MONTHS - 1), last - WINDOW_MONTHS
    p_first = p_last - (WINDOW_MONTHS - 1)
    print(f"  claims to {claims['ACC_DATE'].max().date()}; latest {WINDOW_MONTHS} months {l_first}..{last}, "
          f"prior {p_first}..{p_last}")

    cur = window_totals(ep, claims, l_first, last).add_suffix("_L12")
    pri = window_totals(ep, claims, p_first, p_last).add_suffix("_P12")
    model = model_claims(ep, pp, claims, ep["MONTH"].min(), l_first, last, term_ep)
    df = cur.join(pri, how="left").join(model, how="left").fillna(0)
    df = df[df.EP_L12 > 0]

    admin_rate = S.config["business"]["admin_expense_rate"]
    df["ADMIN_L12"] = df.EP_L12 * admin_rate

    cls = df.groupby(CLASS)[["CLAIMS_L12", "EP_L12", "CLAIM_COUNT_L12"]].transform("sum")
    df["CLASS_LR_L12"] = cls.CLAIMS_L12 / cls.EP_L12
    df["AVG_SEVERITY_L12"] = (df.CLAIMS_L12 / df.CLAIM_COUNT_L12.replace(0, np.nan))
    df["CLASS_AVG_SEVERITY_L12"] = cls.CLAIMS_L12 / cls.CLAIM_COUNT_L12.replace(0, np.nan)

    df["CRED_Z"], k = credibility_z(ep, claims, last, df.EP_L12)

    # Noise band for the trend: standard error of each window's loss ratio under a compound
    # Poisson model, Var(total claims) = sum of squared claim amounts.
    se_l = np.sqrt(df.SUMSQ_L12) / df.EP_L12
    se_p = np.sqrt(df.SUMSQ_P12) / df.EP_P12.replace(0, np.nan)
    df["TREND_SE_PTS"] = 100 * np.sqrt(se_l ** 2 + se_p ** 2)

    cutoff = claims["ACC_DATE"].max().date()
    df["WINDOW_START"] = l_first.to_timestamp().date().isoformat()
    df["WINDOW_END"] = last.to_timestamp(how="end").date().isoformat()
    df["DATA_CUTOFF"] = cutoff.isoformat()
    df["WINDOW_LABEL"] = (f"{l_first.strftime('%b %Y')}–{last.strftime('%b %Y')}"
                          f" · data to {cutoff.strftime('%d %b %Y')}")
    df["CRED_K"] = k if np.isfinite(k) else np.nan

    cols = ["WINDOW_START", "WINDOW_END", "DATA_CUTOFF", "WINDOW_LABEL",
            "EP_L12", "CLAIMS_L12", "CLAIM_COUNT_L12", "EP_P12", "CLAIMS_P12", "CLAIM_COUNT_P12",
            "COMMISSION_L12", "ADMIN_L12", "MODEL_CLAIMS_L12", "CLASS_MODEL_FACTOR",
            "CLASS_LR_L12", "CRED_Z", "CRED_K",
            "TREND_SE_PTS", "AVG_SEVERITY_L12", "CLASS_AVG_SEVERITY_L12"]
    return df.reset_index()[SEG + cols].sort_values(SEG)


# ---------------------------------------------------------------------
# PORTFOLIO CHECKS
# ---------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Cleans text and guarantees a single sentence ending with a period."""
    text = " ".join(str(text).split())
    if not text.endswith("."):
        text += "."
    return text


def make_alert(rule_id: str, severity: str, title: str, issue: str, impact: str, action: str,
               category: str, metric_name: str, metric_value: float, requires_action: bool) -> dict:
    """Creates the standard portfolio alert structure."""
    return {
        "alert_rule_id": rule_id,
        "severity": severity,
        "title": clean_text(title),
        "issue": clean_text(issue),
        "impact": clean_text(impact),
        "action": clean_text(action),
        "zone": "Portfolio",
        "class_desc": "All",
        "policy_type_desc": "All",
        "category": category,
        "metric_name": metric_name,
        "metric_value": metric_value,
        "requires_action": requires_action,
    }


def rule_settlement_speed(dev_factors: pd.DataFrame, cfg: dict) -> list:
    """Claims taking longer to settle than expected."""
    row = dev_factors[dev_factors["transition"] == cfg["settlement_dev_month_check"]]
    if row.empty:
        return []

    deviation = row.iloc[0]["factor"] - 1.0
    if deviation <= cfg["settlement_deviation_threshold"]:
        return []

    transition = cfg["settlement_dev_month_check"]
    return [make_alert(
        rule_id="settlement_speed",
        severity="info",
        title="Portfolio — Claims settling slower than expected",
        issue=(f"Claims at the {transition} month mark are settling {deviation:.1%} "
               f"slower than the historical pattern"),
        impact="Slower closure increases uncertainty in claims reserves",
        action="Review the open claims pipeline with the claims team",
        category="reserving",
        metric_name="dev_factor_deviation",
        metric_value=deviation,
        requires_action=False,
    )]


def rule_model_bias(perf: pd.DataFrame, cfg: dict) -> list:
    """Forecast model consistently over- or under-predicting."""
    glm = perf[(perf["model"] == "glm") & (perf["horizon_label"] == cfg["model_bias_horizon"])]
    if glm.empty or glm["actual"].sum() == 0:
        return []

    bias = glm["signed_error"].sum() / glm["actual"].sum()
    if abs(bias) <= cfg["model_bias_threshold"]:
        return []

    if bias < 0:
        issue = f"The forecast model has under-estimated claim costs by {abs(bias):.0%} over the past year"
        impact = "Actual claims may exceed the forecast used in pricing and planning"
        action = "Build an appropriate buffer into pricing decisions"
    else:
        issue = f"The forecast model has over-estimated claim costs by {abs(bias):.0%} over the past year"
        impact = "Pricing decisions may be based on higher expected claims than observed"
        action = "Validate model results before using them to reduce rates"

    return [make_alert(
        rule_id="model_bias",
        severity="warning",
        title="Forecast model shows material bias",
        issue=issue, impact=impact, action=action,
        category="model",
        metric_name="forecast_bias",
        metric_value=bias,
        requires_action=False,
    )]


ALERT_RULES = [
    {"id": "settlement_speed", "fn": rule_settlement_speed, "needs": "fact_dev_factors_paid.csv"},
    {"id": "model_bias", "fn": rule_model_bias, "needs": "fact_model_performance.csv"},
]

SEVERITY_RANK = {"critical": 1, "warning": 2, "info": 3}

OUTPUT_COLUMNS = [
    "ALERT_ID", "SEVERITY", "SEVERITY_RANK", "TITLE", "ISSUE", "IMPACT", "ACTION",
    "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC", "SEGMENT_LABEL", "CATEGORY",
    "METRIC_NAME", "METRIC_VALUE", "REQUIRES_ACTION", "DETECTED_DATE", "ALERT_RULE_ID",
]


def write_alerts(alerts: list, out_path: Path, cfg: dict) -> None:
    if not alerts:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(out_path, index=False, encoding="utf-8")
        print(f"  0 portfolio alerts written to {out_path}")
        return

    df = pd.DataFrame(alerts)
    df["severity_rank"] = df["severity"].map(SEVERITY_RANK).fillna(9)
    df["abs_metric"] = df["metric_value"].abs()
    df = df.sort_values(["severity_rank", "abs_metric"], ascending=[True, False]).head(cfg["max_alerts"])
    df.columns = [c.upper() for c in df.columns]
    df["ALERT_ID"] = range(1, len(df) + 1)
    df["SEGMENT_LABEL"] = "Portfolio"
    df["DETECTED_DATE"] = cfg["detected_date"]
    df[OUTPUT_COLUMNS].to_csv(out_path, index=False, encoding="utf-8")
    print(f"  {len(df)} portfolio alerts written to {out_path}")


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build management alert inputs")
    parser.add_argument("--facts-dir", default=str(S.facts_dir), help="Path to the 02_facts directory")
    args = parser.parse_args()
    facts = Path(args.facts_dir)

    print("=" * 60)
    print("Management alert inputs")
    print("=" * 60)

    basis = build_alert_basis(facts)
    out = facts / "fact_alert_basis.csv"
    basis.to_csv(out, index=False, encoding="utf-8")
    print(f"  {len(basis)} segments written to {out}")

    # Each segment's reliable loss ratio: the same blend the alerts use. Steps 5 and 9 read it.
    expected = basis[SEG].copy()
    expected["RELIABLE_LR"] = (basis.CRED_Z * basis.CLAIMS_L12 / basis.EP_L12.replace(0, np.nan)
                               + (1 - basis.CRED_Z) * basis.CLASS_LR_L12)
    expected.to_csv(facts / "fact_expected_claims.csv", index=False, encoding="utf-8")

    alerts = []
    for rule in ALERT_RULES:
        path = facts / rule["needs"]
        if not path.exists():
            print(f"  SKIP rule '{rule['id']}': {rule['needs']} not found")
            continue
        result = rule["fn"](pd.read_csv(path, encoding="utf-8"), CONFIG)
        print(f"  Rule '{rule['id']}': {len(result)} alerts")
        alerts.extend(result)
    write_alerts(alerts, facts / "fact_alerts.csv", CONFIG)

    print("\n" + "=" * 60)
    print("Done.")


if __name__ == "__main__":
    main()
