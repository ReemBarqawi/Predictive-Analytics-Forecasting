"""
15_official_alerts.py - the management alerts at the approved target, for the email
====================================================================================

The report decides its alerts in DAX (measures folder "12 Management Alerts") so that the Target CR
slider updates them at once (docs/TARGET_CR.md). A scheduled email cannot depend on a slider, so this
step decides the same alerts at the approved target and writes them with the card text.

The rules below follow the DAX measures one for one (names in brackets). They read the same files
the report reads, so at the default target the list must equal the report's panel:
    fact_alert_basis.csv                    In-Year figures, credibility, trend      (step 11)
    fact_segment_performance_by_year.csv    Today figures of the current policy year (step 5)
    fact_optimal_scenarios.csv              recommended price change                 (step 9)
    dim_report_window.csv                   current year, target, trend factor, reliability bands (step 12)

Nothing on the cards is typed in: every value comes from those files, the target and the reliability
bands from config.toml through dim_report_window. Only the sentence patterns are fixed, in WORDING.

Writes: 02_facts/fact_alerts_official.csv, one row per alert, most severe first.

Usage:
    python pipeline/15_official_alerts.py
"""
import argparse
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from settings import S

SEG = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]

# The sentence patterns of the cards, as in the DAX measures. Values go in the {} fields.
WORDING = {
    "headline_current": "{state}{trend}",
    "state_loss": "Loss-making",
    "state_above": "Above target",
    "trend_worse": " and getting worse",
    "trend_better": " but improving",
    "trend_none": ", no clear trend",
    "headline_future": "Expected to go above target",
    "gap_above": ", SAR {over} above the {target} target",
    "gap_within": ", within the {target} target",
    "change_same": ", the same as {prior_year}",
    "change_up": ", up from {prior} in {prior_year}",
    "change_down": ", down from {prior} in {prior_year}",
    "outlook_flagged": " With no price change, this is expected to reach {expected} in {next_year}.",
    "outlook_improving": " Results are clearly improving, so it is not flagged for {next_year} ({expected} expected).",
    "issue": ("Claims and expenses are estimated at {today_cr} of the SAR {today_premium} premium on {year} policies"
              "{gap}. Actual so far: {actual_cr} of the SAR {earned} earned in {year}{change}.{outlook}"),
    "action_small": "Review this small book manually before changing prices (recommended {price}).",
    "action_improving": "Keep current measures and monitor at the next review.",
    "action_reprice": "Reprice at renewal: recommended {price}. Drill through to Optimal Price for the effect.",
    "evidence": "{level} confidence: based on {claims} claims in {year}.",
    "period": ("Policies written in {year} · actual results {start} to {end} · target combined ratio {target}"
               " · data to {cutoff}"),
    "severity": {2: "Critical", 1: "Warning"},
}


# ---------------------------------------------------------------------
# DAX-equivalent formatting (FORMAT rounds half away from zero)
# ---------------------------------------------------------------------

def _round(x: float, places: int = 0) -> Decimal:
    return Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def pct(x: float) -> str:
    """FORMAT ( x, "0%" )"""
    return f"{_round(x * 100)}%"


def signed_pct(x: float) -> str:
    """FORMAT ( x, "+0%;-0%;0%" )"""
    v = _round(x * 100)
    return f"+{v}%" if v > 0 else (f"{v}%" if v < 0 else "0%")


def sar(x: float) -> str:
    """FORMAT ( x, IF ( x >= 1E6, "#,0,,.0\\M", "#,0,\\K" ) )"""
    if x >= 1e6:
        return f"{_round(x / 1e6, 1):,}M"
    return f"{_round(x / 1e3):,}K"


def count(x: float) -> str:
    """FORMAT ( x, "#,0" )"""
    return f"{_round(x):,}"


# ---------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------

def load_inputs(facts: Path, dims: Path) -> tuple[pd.DataFrame, dict]:
    win = pd.read_csv(dims / "dim_report_window.csv").iloc[0].to_dict()
    year = int(win["CURRENT_YEAR"])

    basis = pd.read_csv(facts / "fact_alert_basis.csv", parse_dates=["WINDOW_START", "WINDOW_END", "DATA_CUTOFF"])

    perf = pd.read_csv(facts / "fact_segment_performance_by_year.csv")
    perf = perf[perf["POLICY_YEAR"] == year]
    today = perf.groupby(SEG).agg(TODAY_PREMIUM=("EARNED_PREMIUM", "sum"), TODAY_CLAIMS=("ULTIMATE_CLAIMS", "sum"),
                                  TODAY_COMMISSION=("COMMISSION_SUM", "sum"), TODAY_ADMIN=("ADMIN_EXPENSE", "sum"))

    # [Rec Price Change]: the base case, weighted by retained exposure
    opt = pd.read_csv(facts / "fact_optimal_scenarios.csv", encoding="cp1252")
    opt = opt[opt["sensitivity_case"] == "base"]
    rec = (opt.assign(w=opt.optimal_premium_change * opt.retained_exposure).groupby(SEG)[["w", "retained_exposure"]].sum())
    rec = (rec.w / rec.retained_exposure.replace(0, np.nan)).rename("REC_PRICE_CHANGE")

    df = basis.set_index(SEG).join(today, how="left").join(rec, how="left").reset_index()
    return df, win


# ---------------------------------------------------------------------
# The alert rules (measures folder "12 Management Alerts")
# ---------------------------------------------------------------------

def decide(df: pd.DataFrame, win: dict) -> pd.DataFrame:
    target = float(win["TARGET_COMBINED_RATIO"])
    rel_low, rel_high = float(win["RELIABILITY_LOW"]), float(win["RELIABILITY_HIGH"])
    trend_factor = float(win["TREND_FACTOR"]) if win["EXPECTED_CLAIMS"] == "experience" else 1.0
    d = df.copy()

    ep = d.EP_L12.replace(0, np.nan)
    d["IN_YEAR_EXPENSES"] = d.COMMISSION_L12 + d.ADMIN_L12
    d["EXPENSE_RATIO"] = d.IN_YEAR_EXPENSES / ep                                   # [Alert Expense Ratio]
    d["IN_YEAR_CR"] = (d.CLAIMS_L12 + d.IN_YEAR_EXPENSES) / ep                      # [In-Year Combined Ratio]
    blended_lr = d.CRED_Z * (d.CLAIMS_L12 / ep) + (1 - d.CRED_Z) * d.CLASS_LR_L12
    d["RELIABLE_CR"] = blended_lr + d.EXPENSE_RATIO                                 # [Reliable Combined Ratio]
    d["EXPECTED_CR"] = (d.RELIABLE_CR - d.EXPENSE_RATIO) * trend_factor + d.EXPENSE_RATIO  # [Expected Combined Ratio]

    # [Alert Trend Pts], [Alert Trend Direction]
    prior_lr = d.CLAIMS_P12 / d.EP_P12.replace(0, np.nan)
    d["TREND_PTS"] = np.where(d.EP_P12 > 0, (d.CLAIMS_L12 / ep - prior_lr) * 100, np.nan)
    has = d.TREND_PTS.notna() & d.TREND_SE_PTS.notna()
    d["TREND_DIRECTION"] = np.where(has & (d.TREND_PTS.abs() > 2 * d.TREND_SE_PTS), np.sign(d.TREND_PTS), 0).astype(int)

    # [Current Flag], [Future Flag], [Alert Level]
    d["CURRENT_FLAG"] = d.RELIABLE_CR > target
    d["FUTURE_FLAG"] = (d.EXPECTED_CR > target) & (d.TREND_DIRECTION != -1)
    flags = d.CURRENT_FLAG.astype(int) + d.FUTURE_FLAG.astype(int)
    low_cred = d.CRED_Z < rel_low
    own_bad = d.IN_YEAR_CR > target
    d["LEVEL"] = np.where(low_cred, np.where(own_bad, np.minimum(flags, 1), 0), flags)
    d = d[d.LEVEL > 0].copy()

    # Today figures, [Alert SAR Above Target], [Alert Sort]
    d["TODAY_EXPENSES"] = d.TODAY_COMMISSION + d.TODAY_ADMIN
    d["TODAY_CR"] = (d.TODAY_CLAIMS + d.TODAY_EXPENSES) / d.TODAY_PREMIUM.replace(0, np.nan)
    d["SAR_ABOVE_TARGET"] = (d.TODAY_CR - target) * d.TODAY_PREMIUM
    d["SORT"] = d.LEVEL * 1e12 + d.SAR_ABOVE_TARGET.fillna(0)
    d["LOW_CREDIBILITY"] = d.CRED_Z < rel_low
    d["CONFIDENCE"] = np.select([d.CRED_Z >= rel_high, d.CRED_Z >= rel_low], ["High", "Medium"], "Low")
    d["TARGET_CR"] = target
    d["CURRENT_YEAR"] = int(win["CURRENT_YEAR"])
    d["PRIORITY"] = priority(d)
    return d.sort_values("SORT", ascending=False)


def priority(d: pd.DataFrame) -> pd.Series:
    """What the email asks management to do with each alert (docs/ALERTS_METHOD.md §11):
        1 Act now               reliable enough to act on (credibility at or above the low band)
        2 Pricing review        limited data, and the outlook stays above target: looks underpriced
        3 Claims investigation  limited data, outlook within target or improving: claims, not price
        4 Monitor only          fewer claims than [credibility] full_claims: too few to conclude
    """
    few_claims = d.CLAIM_COUNT_L12 < int(S.config["credibility"]["full_claims"])
    return pd.Series(np.select([few_claims, ~d.LOW_CREDIBILITY, d.FUTURE_FLAG], [4, 1, 2], 3), index=d.index)


def card_text(r: pd.Series, year: int, target: float) -> dict:
    w = WORDING
    tgt = pct(target)
    trend = {1: w["trend_worse"], -1: w["trend_better"]}.get(int(r.TREND_DIRECTION), w["trend_none"])

    # [Alert Headline]
    if r.CURRENT_FLAG:
        state = w["state_loss"] if r.TODAY_CR > 1 else w["state_above"]
        headline = w["headline_current"].format(state=state, trend=trend)
    else:
        headline = w["headline_future"]

    # [Alert Issue]
    gap = (w["gap_above"].format(over=sar(r.SAR_ABOVE_TARGET), target=tgt) if r.CURRENT_FLAG
           else w["gap_within"].format(target=tgt))
    if pd.isna(r.TREND_PTS):
        change = ""
    else:
        prior = pct(r.IN_YEAR_CR - r.TREND_PTS / 100)
        if _round(r.TREND_PTS) == 0:
            change = w["change_same"].format(prior_year=year - 1)
        elif r.TREND_PTS > 0:
            change = w["change_up"].format(prior=prior, prior_year=year - 1)
        else:
            change = w["change_down"].format(prior=prior, prior_year=year - 1)
    outlook = (w["outlook_flagged"] if r.FUTURE_FLAG else w["outlook_improving"]).format(
        expected=pct(r.EXPECTED_CR), next_year=year + 1)
    issue = w["issue"].format(today_cr=pct(r.TODAY_CR), today_premium=sar(r.TODAY_PREMIUM), year=year, gap=gap,
                              actual_cr=pct(r.IN_YEAR_CR), earned=sar(r.EP_L12), change=change, outlook=outlook)

    # [Alert Action]
    price = signed_pct(r.REC_PRICE_CHANGE) if pd.notna(r.REC_PRICE_CHANGE) else ""
    if r.LOW_CREDIBILITY:
        action = w["action_small"].format(price=price)
    elif r.TREND_DIRECTION == -1:
        action = w["action_improving"]
    else:
        action = w["action_reprice"].format(price=price)

    # [Alert Evidence], [Alert Period]
    evidence = w["evidence"].format(level=r.CONFIDENCE, claims=count(r.CLAIM_COUNT_L12), year=year)
    period = w["period"].format(year=year, start=r.WINDOW_START.strftime("%b %Y"), end=r.WINDOW_END.strftime("%b %Y"),
                                target=tgt, cutoff=f"{r.DATA_CUTOFF.day} {r.DATA_CUTOFF.strftime('%b %Y')}")
    return {"SEVERITY": w["severity"][int(r.LEVEL)], "HEADLINE": headline, "ISSUE": issue, "ACTION": action,
            "EVIDENCE": evidence, "PERIOD": period}


OUTPUT_COLUMNS = [
    "ALERT_RANK", "SEVERITY", "LEVEL", "PRIORITY", "ZONE", "CLASS_DESC", "POLICY_TYPE_DESC", "SEGMENT_LABEL",
    "HEADLINE", "ISSUE", "ACTION", "EVIDENCE", "PERIOD",
    "SAR_ABOVE_TARGET", "TODAY_PREMIUM", "TODAY_CR", "EP_L12", "IN_YEAR_CR", "RELIABLE_CR", "EXPECTED_CR",
    "CURRENT_FLAG", "FUTURE_FLAG", "LOW_CREDIBILITY",
    "TREND_PTS", "TREND_DIRECTION", "REC_PRICE_CHANGE", "CRED_Z", "CONFIDENCE", "CLAIM_COUNT_L12",
    "TARGET_CR", "CURRENT_YEAR", "WINDOW_START", "WINDOW_END", "DATA_CUTOFF",
]


def build(facts: Path, dims: Path) -> pd.DataFrame:
    df, win = load_inputs(facts, dims)
    d = decide(df, win)
    if d.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    year, target = int(win["CURRENT_YEAR"]), float(win["TARGET_COMBINED_RATIO"])
    text = pd.DataFrame([card_text(r, year, target) for _, r in d.iterrows()], index=d.index)
    d = d.join(text)
    d["SEGMENT_LABEL"] = d.ZONE + " · " + d.CLASS_DESC + " · " + d.POLICY_TYPE_DESC
    d["ALERT_RANK"] = range(1, len(d) + 1)
    for c in ["WINDOW_START", "WINDOW_END", "DATA_CUTOFF"]:
        d[c] = d[c].dt.date.astype(str)
    return d[OUTPUT_COLUMNS]


def main():
    parser = argparse.ArgumentParser(description="Management alerts at the approved target")
    parser.add_argument("--facts-dir", default=str(S.facts_dir))
    parser.add_argument("--dims-dir", default=str(S.dims_dir))
    args = parser.parse_args()

    print("=" * 60)
    print("Official management alerts (approved target)")
    print("=" * 60)
    out = build(Path(args.facts_dir), Path(args.dims_dir))
    path = Path(args.facts_dir) / "fact_alerts_official.csv"
    out.to_csv(path, index=False, encoding="utf-8")
    n_crit = int((out.SEVERITY == "Critical").sum())
    print(f"  {len(out)} alerts ({n_crit} critical, {len(out) - n_crit} warning) written to {path}")


if __name__ == "__main__":
    main()
