"""
09_underwriting_pnl.py  --  Underwriting P&L statement + variance bridge for the optimizer
Runs AFTER 08_profit_optimizer.py.

Reads : fact_scenario_results.csv, fact_optimal_scenarios.csv   (from 08)
Writes: fact_uw_pnl.csv          long-format statement, one row per case x segment x line item
        fact_uw_pnl_bridge.csv   profit bridge (No-change -> Recommended), reconciles exactly

Columns compared
  No-change renewal : 0% premium change, baseline renewal assumption (same basis the optimizer uses)
  Recommended       : the selected scenario per segment (after the portfolio-retention repair)

Basis: Annualized Renewal Run-Rate, synthetic data, retention parameters are ASSUMPTIONS.
This is a management view, not a statutory (IFRS 17) income statement.
"""
import os
import sys
import numpy as np
import pandas as pd

BASE = r"C:\predicting insurance\SYNTHETIC_DSS_DATA\02_facts"
if len(sys.argv) > 1:
    BASE = sys.argv[1]

MATERIALITY = {            # a variance is material if EITHER threshold is exceeded
    "pct": 0.05,           # 5% of the No-change line
    "share_of_premium": 0.01,   # 1% of No-change portfolio premium (scales with the book)
    "ratio_pts": 1.0,      # 1 pt for ratio lines
}
ID = ["segment_id", "sensitivity_case"]
AMOUNTS = ["scenario_premium", "expected_claims", "expected_commission",
           "expected_admin_expense", "expected_total_expenses", "underwriting_profit",
           "retained_exposure", "retained_policy_count"]


def load():
    res = pd.read_csv(os.path.join(BASE, "fact_scenario_results.csv"))
    opt = pd.read_csv(os.path.join(BASE, "fact_optimal_scenarios.csv"))
    nochg = res[np.isclose(res["premium_change"], 0.0)][ID + ["premium_change"] + AMOUNTS]
    # fact_optimal_scenarios drops the commission/admin split, so take the full row
    # from fact_scenario_results at the selected premium change.
    sel = opt[ID + ["optimal_premium_change", "contraction_flag", "selection_note"]].copy()
    sel["k"] = sel["optimal_premium_change"].round(4)
    res["k"] = res["premium_change"].round(4)
    rec = sel.merge(res[ID + ["k"] + AMOUNTS], on=ID + ["k"], how="left", validate="1:1")
    if rec["scenario_premium"].isna().any():
        raise ValueError("Recommended scenario not found in fact_scenario_results")
    seg = nochg.merge(rec, on=ID, suffixes=("_0", "_1"), validate="1:1")
    return seg


def statement_lines(d0, d1):
    """d0/d1: dicts of summed amounts. Returns list of (order, line, v0, v1, is_ratio)."""
    def r(a, b):
        return a / b if b else np.nan
    L = []
    L.append((10, "Expected premium",           d0["scenario_premium"], d1["scenario_premium"], False))
    L.append((20, "Expected claims",            -d0["expected_claims"], -d1["expected_claims"], False))
    L.append((30, "Commission",                 -d0["expected_commission"], -d1["expected_commission"], False))
    L.append((40, "Admin expense",              -d0["expected_admin_expense"], -d1["expected_admin_expense"], False))
    L.append((50, "Total expenses",             -d0["expected_total_expenses"], -d1["expected_total_expenses"], False))
    L.append((60, "UNDERWRITING PROFIT",        d0["underwriting_profit"], d1["underwriting_profit"], False))
    L.append((70, "Loss ratio",                 r(d0["expected_claims"], d0["scenario_premium"]),
                                                r(d1["expected_claims"], d1["scenario_premium"]), True))
    L.append((80, "Expense ratio",              r(d0["expected_total_expenses"], d0["scenario_premium"]),
                                                r(d1["expected_total_expenses"], d1["scenario_premium"]), True))
    L.append((90, "Combined ratio",             r(d0["expected_claims"] + d0["expected_total_expenses"], d0["scenario_premium"]),
                                                r(d1["expected_claims"] + d1["expected_total_expenses"], d1["scenario_premium"]), True))
    L.append((100, "Underwriting margin",       r(d0["underwriting_profit"], d0["scenario_premium"]),
                                                r(d1["underwriting_profit"], d1["scenario_premium"]), True))
    L.append((110, "Retained exposure (yrs)",   d0["retained_exposure"], d1["retained_exposure"], False))
    L.append((120, "Retained policies",         d0["retained_policy_count"], d1["retained_policy_count"], False))
    return L


def bridge(s):
    """Profit bridge per segment row. Order is fixed and documented; it is a management
    attribution, not a unique causal decomposition. Sums exactly to profit change.
      1 Rate          : price change on the No-change premium (volume held)
      2 Commission on rate : commission on that extra premium
      3 Volume        : change in retained exposure at new-price unit economics, mix held
      4 Premium mix   : premium shift because the policies that left had different premiums
      5 Claims risk-mix : claims shift because the policies that left had different risk
      6 Expense mix   : expense shift from the composition change
    External frequency/severity shocks are applied equally to both columns, so they do not
    appear here; they would only show if the two columns used different shock settings.
    """
    pc = s["optimal_premium_change"]
    P0, C0 = s["scenario_premium_0"], s["expected_claims_0"]
    K0, A0 = s["expected_commission_0"], s["expected_admin_expense_0"]
    P1, C1 = s["scenario_premium_1"], s["expected_claims_1"]
    K1, A1 = s["expected_commission_1"], s["expected_admin_expense_1"]
    sc = s["retained_exposure_1"] / s["retained_exposure_0"]       # volume scale
    steps = {
        "Rate": P0 * pc,
        "Commission on rate": -K0 * pc,
        "Volume (retention)": (sc - 1) * (P0 * (1 + pc) - C0 - K0 * (1 + pc) - A0),
        "Premium mix": P1 - sc * P0 * (1 + pc),
        "Claims risk-mix": -(C1 - sc * C0),
        "Expense mix": -((K1 + A1) - sc * (K0 * (1 + pc) + A0)),
    }
    return steps


def main():
    seg = load()
    pnl_rows, br_rows = [], []

    for case, g in seg.groupby("sensitivity_case"):
        port_prem0 = g["scenario_premium_0"].sum()
        groups = [("PORTFOLIO", g)] + list(g.groupby("segment_id"))
        for seg_id, h in groups:
            d0 = {c: h[f"{c}_0"].sum() for c in AMOUNTS}
            d1 = {c: h[f"{c}_1"].sum() for c in AMOUNTS}
            for order, line, v0, v1, is_ratio in statement_lines(d0, d1):
                var = v1 - v0
                if is_ratio:
                    var_pct = np.nan
                    material = abs(var) * 100 >= MATERIALITY["ratio_pts"]
                else:
                    var_pct = var / abs(v0) if v0 else np.nan
                    material = (abs(var) >= MATERIALITY["share_of_premium"] * port_prem0
                                or (not np.isnan(var_pct) and abs(var_pct) >= MATERIALITY["pct"]))
                # favourable = profit goes up (lines are signed so that + helps profit)
                fav = "Fav" if var > 0 else ("Unfav" if var < 0 else "-")
                if line in ("Loss ratio", "Expense ratio", "Combined ratio"):
                    fav = "Fav" if var < 0 else ("Unfav" if var > 0 else "-")
                if line.startswith("Retained"):
                    fav = "-"
                pnl_rows.append(dict(sensitivity_case=case, segment_id=seg_id, line_order=order,
                                     line_item=line, no_change=v0, recommended=v1, variance=var,
                                     variance_pct=var_pct, is_ratio=is_ratio,
                                     direction=fav, is_material=bool(material)))
            # bridge: computed per segment, summed for the portfolio
            steps = {}
            for _, s in h.iterrows():
                for k, v in bridge(s).items():
                    steps[k] = steps.get(k, 0.0) + v
            order = 0
            br_rows.append(dict(sensitivity_case=case, segment_id=seg_id, step_order=order,
                                step="No-change profit", amount=d0["underwriting_profit"]))
            for k, v in steps.items():
                order += 1
                br_rows.append(dict(sensitivity_case=case, segment_id=seg_id, step_order=order,
                                    step=k, amount=v))
            br_rows.append(dict(sensitivity_case=case, segment_id=seg_id, step_order=order + 1,
                                step="Recommended profit", amount=d1["underwriting_profit"]))
            gap = d0["underwriting_profit"] + sum(steps.values()) - d1["underwriting_profit"]
            assert abs(gap) < 1e-6 * max(1.0, abs(d1["underwriting_profit"])), f"bridge off by {gap}"

    pnl = pd.DataFrame(pnl_rows)
    br = pd.DataFrame(br_rows)
    pnl.to_csv(os.path.join(BASE, "fact_uw_pnl.csv"), index=False, encoding="utf-8")
    br.to_csv(os.path.join(BASE, "fact_uw_pnl_bridge.csv"), index=False, encoding="utf-8")

    # console print: portfolio statement for each case
    for case in ["low", "base", "high"]:
        p = pnl[(pnl.sensitivity_case == case) & (pnl.segment_id == "PORTFOLIO")]
        if p.empty:
            continue
        print(f"\nUNDERWRITING P&L  [{case} sensitivity]  (SAR, annualized renewal run-rate)")
        print(f"{'':28s}{'No-change':>14s}{'Recommended':>14s}{'Var':>13s}{'Var %':>9s}  ")
        for _, r in p.iterrows():
            if r.is_ratio:
                print(f"{r.line_item:28s}{r.no_change:>13.1%} {r.recommended:>13.1%} "
                      f"{r.variance*100:>+10.1f}pt {'':>8s}  {r.direction}{' *' if r.is_material else ''}")
            else:
                vp = "" if pd.isna(r.variance_pct) else f"{r.variance_pct:+.1%}"
                print(f"{r.line_item:28s}{r.no_change:>14,.0f}{r.recommended:>14,.0f}"
                      f"{r.variance:>+13,.0f}{vp:>9s}  {r.direction}{' *' if r.is_material else ''}")
        b = br[(br.sensitivity_case == case) & (br.segment_id == "PORTFOLIO")]
        print("  Bridge: " + "  ".join(f"{s}={a:+,.0f}" for s, a in zip(b.step, b.amount)))


if __name__ == "__main__":
    main()