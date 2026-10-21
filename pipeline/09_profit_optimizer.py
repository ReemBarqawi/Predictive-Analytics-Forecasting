"""
09_profit_optimizer.py  --  Phase 1 PROTOTYPE  (v0.3)
Constrained Retained-Portfolio Profit Optimization (Annualized Renewal Run-Rate)

Reads : SYNTHETIC_DSS_DATA/02_facts/fact_policy_pricing.csv
Writes: SYNTHETIC_DSS_DATA/02_facts/fact_scenario_results.csv
        SYNTHETIC_DSS_DATA/02_facts/fact_optimal_scenarios.csv
        SYNTHETIC_DSS_DATA/02_facts/fact_optimizer_run.csv   (run record)
        SYNTHETIC_DSS_DATA/02_facts/fact_floor_sensitivity.csv  (portfolio floor what-if)

Every number depends on the ASSUMPTIONS block below. Retention parameters are
ASSUMPTIONS (no renewal history exists) -- never present them as calibrated.

Changes in v0.2
  A. Diagnostics: premium coverage, portfolio retention, breach / step-down / no-feasible counts
  B. binding_constraint column: why the engine stopped at the chosen price
  C. 1% premium grid (was 11 coarse steps)
  D. Technical-price anchor: no segment priced above its technical premium + tolerance
  +  fact_optimal_scenarios now carries commission, admin, loss/expense ratio and the
     no-change baseline, so Power BI can read it alone

Changes in v0.3
  E. Credibility fallback: segments under min_segment_policies are no longer skipped. They
     take the price chosen for ZONE x CLASS, else CLASS, and keep their own premium, claims
     and retention at that price. New columns: credibility_level, decision_source.
  F. Floor sensitivity: the portfolio retention floor is re-run at 80/82/85/88%
     -> fact_floor_sensitivity.csv (one row per case x floor).
"""
import os
import sys
import numpy as np
import pandas as pd

# ------------------------------------------------------------------ CONFIG
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SYNTHETIC_DSS_DATA", "02_facts")
if len(sys.argv) > 1:               # allows: python pipeline/09_profit_optimizer.py <folder>
    BASE = sys.argv[1]

ASSUMPTIONS = {
    "model_version": "proto-0.3",
    "policy_year": None,            # None = latest POLICY_YEAR in the file
    # C. 1% steps from -10% to +50%
    "premium_grid": [round(x, 2) for x in np.arange(-0.10, 0.5001, 0.01)],
    "baseline_renewal": 0.90,       # P(renewal) at 0% change.            [Assumption]
    # logit slope per +100% premium change  (bigger = customers more price sensitive)
    "sensitivity_cases": {"low": 3.0, "base": 5.0, "high": 8.0},   # [Assumption]
    # risk heterogeneity: low-risk policies react more (1+gamma), high-risk less (1-gamma)
    "risk_gamma": 0.5,              # 0 switches the risk-mix effect OFF. [Assumption]
    "admin_rate_of_baseline_premium": 0.10,   # matches 01_build_synthetic_data.py. Fixed PER POLICY.
    # external shocks (independent of pricing decision)                 [External]
    "ext_freq_trend": 0.0,
    "ext_severity_inflation": 0.0,
    # D. technical-price anchor                                          [Assumption]
    "target_margin": 0.05,          # profit loading in the technical premium
    "tech_tolerance": 0.05,         # may price up to 5 pts above the technical change
    # constraints (management parameters)
    "retention_floor": 0.80,        # segment exposure-weighted retention
    "portfolio_retention_floor": 0.85,
    "premium_change_min": -0.20,
    "premium_change_max": 0.50,
    "min_profit_improvement": 0.0,  # vs. 0%-change scenario, same retention basis
    "contraction_flag_threshold": 0.10,   # flag if retained exposure falls >10% vs. 0% scenario
    "min_segment_policies": 30,     # skip tiny segments (credibility)
    "target_combined_ratio": 0.95,
    # F. portfolio floors to compare (the selected one is portfolio_retention_floor)
    "floor_sensitivity": [0.80, 0.82, 0.85, 0.88],
}
SEG = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]


# ------------------------------------------------------------------ DATA
def load_policies(a):
    df = pd.read_csv(os.path.join(BASE, "fact_policy_pricing.csv"))
    years = sorted(df["POLICY_YEAR"].dropna().unique().astype(int))
    yr = a["policy_year"] or years[-1]
    print(f"Policy years in file: {years[0]}-{years[-1]}  -> using {yr}")
    df = df[df["POLICY_YEAR"] == yr].copy()
    n_raw = len(df)
    # drop cancellations / negative premium / zero exposure (same logic as 'Total Charged Premium')
    df = df[(df["charged_premium"] > 0) & (df["EXPOSURE_YEARS"] > 0)].copy()
    df = df.dropna(subset=["expected_frequency_per_year", "expected_severity"])
    print(f"Rows kept after cleaning: {len(df):,} of {n_raw:,}")

    df["base_premium"] = df["charged_premium"]
    df["commission_rate"] = (df["COMMISSION"] / df["TOTAL_PREMIUM"]).clip(0, 0.5).fillna(0)
    df["admin_per_policy"] = a["admin_rate_of_baseline_premium"] * df["base_premium"]  # fixed per policy
    # model claims: exposure x frequency x severity (+ external shocks)
    df["claims_if_retained"] = (df["EXPOSURE_YEARS"] * df["expected_frequency_per_year"]
                                * (1 + a["ext_freq_trend"])
                                * df["expected_severity"] * (1 + a["ext_severity_inflation"]))
    # D. technical premium = expected claims / (1 - commission - admin - margin)
    load = (1 - df["commission_rate"] - a["admin_rate_of_baseline_premium"]
            - a["target_margin"]).clip(lower=0.30)
    df["technical_premium"] = df["claims_if_retained"] / load
    # risk percentile inside segment (0 = lowest risk, 1 = highest) from pure premium
    df["risk_pct"] = (df.groupby(SEG)["pure_premium_per_year"]
                        .rank(pct=True, method="average"))
    return df, yr


# ------------------------------------------------------------------ RETENTION
def logit(p):
    return np.log(p / (1 - p))


def retention_prob(pc, k, risk_pct, a):
    """P(renewal_i) = 1/(1+exp(-Z)); Z = logit(P0) - k_i * pc"""
    k_i = k * (1 + a["risk_gamma"] * (0.5 - risk_pct) * 2)   # avg ~ k
    z = logit(a["baseline_renewal"]) - k_i * pc
    return 1 / (1 + np.exp(-z))


def implied_arc_elasticity(k, a, pc=0.10):
    """Sanity check: % change in retention / % change in price."""
    p0 = a["baseline_renewal"]
    p1 = 1 / (1 + np.exp(-(logit(p0) - k * pc)))
    return ((p1 - p0) / p0) / pc


# ------------------------------------------------------------------ SCENARIOS
def evaluate(g, pc, k, a):
    p = retention_prob(pc, k, g["risk_pct"].values, a)
    expo = g["EXPOSURE_YEARS"].values
    retained_expo = expo * p
    premium = (g["base_premium"].values * (1 + pc) * p).sum()
    claims = (g["claims_if_retained"].values * p).sum()
    commission = (g["base_premium"].values * (1 + pc) * p * g["commission_rate"].values).sum()
    admin = (g["admin_per_policy"].values * p).sum()          # per retained policy
    expenses = commission + admin
    profit = premium - claims - expenses
    ret_expo_total = retained_expo.sum()
    return {
        "premium_change": pc,
        "scenario_premium": premium,
        "retention": ret_expo_total / expo.sum(),             # exposure-weighted
        "retained_policy_count": p.sum(),
        "retained_exposure": ret_expo_total,
        "original_exposure": expo.sum(),
        # risk mix: exposure-weighted pure premium of retained book
        "risk_mix": (retained_expo * g["pure_premium_per_year"].values).sum() / ret_expo_total,
        "frequency": (retained_expo * g["expected_frequency_per_year"].values).sum() / ret_expo_total,
        "severity": (p * g["claims_if_retained"].values).sum()
                    / max((p * expo * g["expected_frequency_per_year"].values).sum(), 1e-9),
        "expected_claims": claims,
        "expected_commission": commission,
        "expected_admin_expense": admin,
        "expected_total_expenses": expenses,
        "loss_ratio": claims / premium if premium > 0 else np.nan,
        "expense_ratio": expenses / premium if premium > 0 else np.nan,
        "combined_ratio": (claims + expenses) / premium if premium > 0 else np.nan,
        "underwriting_profit": profit,
    }


def run_segment(g, seg_key, case, k, a):
    rows = [evaluate(g, pc, k, a) for pc in a["premium_grid"]]
    res = pd.DataFrame(rows)
    base = res.loc[np.isclose(res["premium_change"], 0.0)].iloc[0]   # 0%-change, same retention basis
    tech_change = g["technical_premium"].sum() / g["base_premium"].sum() - 1
    tech_cap = max(tech_change, 0.0) + a["tech_tolerance"]

    res["profit_change"] = res["underwriting_profit"] - base["underwriting_profit"]
    res["exposure_change_vs_base"] = res["retained_exposure"] / base["retained_exposure"] - 1
    # each rule kept separately so we can say which one binds
    res["pass_retention"] = res["retention"] >= a["retention_floor"]
    res["pass_range"] = res["premium_change"].between(a["premium_change_min"], a["premium_change_max"])
    res["pass_technical"] = res["premium_change"] <= tech_cap + 1e-9
    res["pass_profit"] = res["profit_change"] >= a["min_profit_improvement"]
    res["is_feasible"] = (res["pass_retention"] & res["pass_range"]
                          & res["pass_technical"] & res["pass_profit"])
    res["contraction_flag"] = res["exposure_change_vs_base"] < -a["contraction_flag_threshold"]
    res["technical_change"] = tech_change
    for col, v in zip(SEG, seg_key):
        res[col] = v
    res["segment_id"] = " | ".join(seg_key)
    res["sensitivity_case"] = case
    res["retention_floor"] = a["retention_floor"]
    # no-change baseline carried on every row (used by Power BI and 10_underwriting_pnl)
    res["nochange_premium"] = base["scenario_premium"]
    res["nochange_claims"] = base["expected_claims"]
    res["nochange_expenses"] = base["expected_total_expenses"]
    res["nochange_profit"] = base["underwriting_profit"]
    res["nochange_combined_ratio"] = base["combined_ratio"]
    res["nochange_retained_exposure"] = base["retained_exposure"]
    return res


def binding_reason(res, r):
    """B. Why did the engine not go one step higher?"""
    nxt = res[res["premium_change"] > r["premium_change"] + 1e-9].head(1)
    if nxt.empty:
        return "grid maximum"
    n = nxt.iloc[0]
    if not n["pass_range"]:
        return "price cap"
    if not n["pass_technical"]:
        return "technical price"
    if not n["pass_retention"]:
        return "retention floor"
    return "profit peak"          # next step is allowed but earns less


def pick_optimal(res):
    feas = res[res["is_feasible"]]
    if feas.empty:                      # nothing passes -> fall back to 0% and say so
        r = res.loc[np.isclose(res["premium_change"], 0.0)].iloc[0].copy()
        r["selection_note"] = "NO FEASIBLE SCENARIO - holding current price"
        r["binding_constraint"] = "none feasible"
        return r
    r = feas.loc[feas["underwriting_profit"].idxmax()].copy()
    r["selection_note"] = "highest-profit feasible"
    r["binding_constraint"] = binding_reason(res, r)
    return r


def port_retention(o):
    return o["retained_exposure"].sum() / o["original_exposure"].sum()


def portfolio_repair(opt, results, a, floor=None):
    """Portfolio retention floor couples segments. Prototype repair: while the portfolio
    is below the floor, step the segment with the lowest profit-lost-per-exposure-regained
    down one grid step (only to steps that are feasible)."""
    floor = a["portfolio_retention_floor"] if floor is None else floor
    opt = opt.copy()
    grid = np.array(sorted(a["premium_grid"]))

    guard = 0
    while port_retention(opt) < floor and guard < 5000:
        guard += 1
        best, best_cost, best_row = None, np.inf, None
        for i, r in opt.iterrows():
            idx = int(np.argmin(np.abs(grid - r["premium_change"])))
            if idx == 0:
                continue
            cand = results[(results["segment_id"] == r["segment_id"]) &
                           (results["sensitivity_case"] == r["sensitivity_case"]) &
                           np.isclose(results["premium_change"], grid[idx - 1]) &
                           results["is_feasible"]]
            if cand.empty:
                continue
            c = cand.iloc[0]
            d_ret = c["retained_exposure"] - r["retained_exposure"]
            d_profit = r["underwriting_profit"] - c["underwriting_profit"]
            if d_ret > 0 and d_profit / d_ret < best_cost:
                best, best_cost, best_row = i, d_profit / d_ret, c
        if best is None:
            break
        cols = best_row.index.intersection(opt.columns)
        opt.loc[best, cols] = best_row[cols]
        opt.at[best, "selection_note"] = "stepped down for portfolio retention floor"
        opt.at[best, "binding_constraint"] = "portfolio retention floor"
    opt["portfolio_floor_breached"] = port_retention(opt) < floor
    return opt


# ------------------------------------------------------------------ CREDIBILITY FALLBACK
def parent_levels(seg_key):
    """E. Where a small segment borrows its decision from, most specific first."""
    zone, cls, _ = seg_key
    return [("zone x class", {"ZONE": zone, "CLASS_DESC": cls}),
            ("class", {"CLASS_DESC": cls})]


def choose_all_segments(df, case, k, a, parent_cache):
    """Run every segment on its own policies. Credible segments choose their own price;
    small segments take the price chosen for their nearest credible parent group and
    keep their own premium, claims and retention at that price."""
    results, picks = [], []
    for key, g in df.groupby(SEG):
        res = run_segment(g, key, case, k, a)
        results.append(res)
        if len(g) >= a["min_segment_policies"]:
            r = pick_optimal(res)
            r["credibility_level"] = "segment"
            r["decision_source"] = r["segment_id"]
            picks.append(r)
            continue
        r = None
        for level, filt in parent_levels(key):
            mask = np.logical_and.reduce([df[c] == v for c, v in filt.items()])
            parent = df[mask]
            if len(parent) < a["min_segment_policies"]:
                continue
            pkey = (case, level, tuple(filt.values()))
            if pkey not in parent_cache:
                pid = tuple(filt.get(c, "ALL") for c in SEG)
                parent_cache[pkey] = pick_optimal(run_segment(parent, pid, case, k, a))
            p = parent_cache[pkey]
            r = res.loc[np.isclose(res["premium_change"], p["premium_change"])].iloc[0].copy()
            r["selection_note"] = f"borrowed from {level} ({len(parent)} policies)"
            r["binding_constraint"] = p["binding_constraint"]
            r["credibility_level"] = level
            r["decision_source"] = p["segment_id"]
            break
        if r is None:                       # no credible parent at all
            r = res.loc[np.isclose(res["premium_change"], 0.0)].iloc[0].copy()
            r["selection_note"] = "not credible - holding current price"
            r["binding_constraint"] = "not credible"
            r["credibility_level"] = "none"
            r["decision_source"] = ""
        picks.append(r)
    return pd.concat(results, ignore_index=True), pd.DataFrame(picks).reset_index(drop=True)


def floor_sensitivity(picks, results, case, a):
    """F. What the portfolio looks like under different portfolio retention floors."""
    rows = []
    for fl in a["floor_sensitivity"]:
        o = portfolio_repair(picks, results, a, floor=fl)
        prem = o["scenario_premium"].sum()
        rows.append({
            "sensitivity_case": case,
            "portfolio_retention_floor": fl,
            "portfolio_retention": port_retention(o),
            "expected_premium": prem,
            "expected_profit": o["underwriting_profit"].sum(),
            "profit_change": o["profit_change"].sum(),
            "combined_ratio": (o["expected_claims"].sum() + o["expected_total_expenses"].sum()) / prem,
            "median_premium_change": o["premium_change"].median(),
            "segments_stepped_down": int((o["selection_note"]
                                          == "stepped down for portfolio retention floor").sum()),
            "floor_breached": bool(o["portfolio_floor_breached"].any()),
            "is_selected_floor": np.isclose(fl, a["portfolio_retention_floor"]),
        })
    return rows


# ------------------------------------------------------------------ MAIN
def main():
    a = ASSUMPTIONS
    df, yr = load_policies(a)
    print(f"Policy year {yr}: {len(df):,} policies, "
          f"{df['base_premium'].sum():,.0f} baseline premium")
    for case, k in a["sensitivity_cases"].items():
        print(f"  case {case:5s} k={k}: implied arc elasticity at +10% = "
              f"{implied_arc_elasticity(k, a):.2f}")

    # A. coverage of the credibility rule
    sizes = df.groupby(SEG).size()
    kept_keys = sizes[sizes >= a["min_segment_policies"]].index
    covered = df.set_index(SEG).loc[kept_keys, "base_premium"].sum()
    print(f"Segments: {len(sizes)} total, {len(kept_keys)} credible on their own "
          f"(>= {a['min_segment_policies']} policies) covering "
          f"{covered / df['base_premium'].sum():.0%} of premium; the rest borrow from a parent group")

    all_res, all_opt, all_floor = [], [], []
    parent_cache = {}
    for case, k in a["sensitivity_cases"].items():
        case_res, picks = choose_all_segments(df, case, k, a, parent_cache)
        all_floor += floor_sensitivity(picks, case_res, case, a)
        opt = portfolio_repair(picks, case_res, a)
        all_res.append(case_res)
        all_opt.append(opt)

    results = pd.concat(all_res, ignore_index=True)
    opt = pd.concat(all_opt, ignore_index=True)
    results["model_version"] = a["model_version"]
    results["scenario_date"] = pd.Timestamp.today().date()
    results["scenario_id"] = (results["segment_id"] + "|" + results["sensitivity_case"]
                              + "|" + results["premium_change"].map("{:+.2f}".format))

    opt = opt.rename(columns={
        "premium_change": "optimal_premium_change", "scenario_premium": "optimal_premium",
        "retention": "expected_retention", "frequency": "expected_frequency",
        "severity": "expected_severity", "expected_total_expenses": "expected_expenses",
        "underwriting_profit": "expected_profit"})
    opt["remaining_profitability_gap_pts"] = (
        (opt["combined_ratio"] - a["target_combined_ratio"]).clip(lower=0) * 100)
    opt["model_version"] = a["model_version"]
    opt["optimization_date"] = pd.Timestamp.today().date()
    keep = SEG + ["segment_id", "sensitivity_case", "optimal_premium_change", "optimal_premium",
                  "technical_change", "expected_retention", "retained_exposure",
                  "original_exposure", "expected_frequency", "expected_severity",
                  "expected_claims", "expected_commission", "expected_admin_expense",
                  "expected_expenses", "expected_profit", "profit_change",
                  "loss_ratio", "expense_ratio", "combined_ratio",
                  "nochange_premium", "nochange_claims", "nochange_expenses",
                  "nochange_profit", "nochange_combined_ratio", "nochange_retained_exposure",
                  "contraction_flag", "selection_note", "binding_constraint",
                  "credibility_level", "decision_source", "portfolio_floor_breached", "remaining_profitability_gap_pts",
                  "model_version", "optimization_date"]
    opt = opt[keep]

    run = pd.DataFrame([{**{k: str(v) for k, v in a.items()}, "policy_year": yr,
                         "run_ts": pd.Timestamp.now()}])
    results.to_csv(os.path.join(BASE, "fact_scenario_results.csv"), index=False, encoding="utf-8")
    opt.to_csv(os.path.join(BASE, "fact_optimal_scenarios.csv"), index=False, encoding="utf-8")
    run.to_csv(os.path.join(BASE, "fact_optimizer_run.csv"), index=False, encoding="utf-8")
    floor_tbl = pd.DataFrame(all_floor)
    floor_tbl["model_version"] = a["model_version"]
    floor_tbl.to_csv(os.path.join(BASE, "fact_floor_sensitivity.csv"), index=False, encoding="utf-8")

    # A. portfolio summary by case
    print()
    for case in a["sensitivity_cases"]:
        s = opt[opt["sensitivity_case"] == case]
        p0, p1 = s["nochange_premium"].sum(), s["optimal_premium"].sum()
        cr0 = (s["nochange_claims"].sum() + s["nochange_expenses"].sum()) / p0
        cr1 = (s["expected_claims"].sum() + s["expected_expenses"].sum()) / p1
        print(f"[{case}] no-change profit={s['nochange_profit'].sum():,.0f} (CR {cr0:.1%})  "
              f"recommended profit={s['expected_profit'].sum():,.0f} (CR {cr1:.1%})  "
              f"change={s['profit_change'].sum():+,.0f}")
        print(f"       median change={s['optimal_premium_change'].median():+.0%}  "
              f"portfolio retention={port_retention(s):.1%}  "
              f"floor breached={bool(s['portfolio_floor_breached'].any())}  "
              f"contraction flags={int(s['contraction_flag'].sum())}")
        counts = s["binding_constraint"].value_counts()
        print("       binding: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        prem = s.groupby("credibility_level")["nochange_premium"].sum() / p0
        print("       credibility (share of premium): "
              + ", ".join(f"{k}={v:.0%}" for k, v in prem.items()))

    print("\nPortfolio retention floor sensitivity")
    print(f"{'case':6s}{'floor':>7s}{'retention':>11s}{'profit':>13s}{'CR':>8s}{'median':>8s}{'stepped':>9s}")
    for _, r in floor_tbl.iterrows():
        mark = "  <- selected" if r["is_selected_floor"] else ""
        print(f"{r['sensitivity_case']:6s}{r['portfolio_retention_floor']:>7.0%}"
              f"{r['portfolio_retention']:>11.1%}{r['expected_profit']:>13,.0f}"
              f"{r['combined_ratio']:>8.1%}{r['median_premium_change']:>+8.0%}"
              f"{r['segments_stepped_down']:>9d}{mark}")


if __name__ == "__main__":
    main()