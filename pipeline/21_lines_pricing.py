"""
pipeline/21_lines_pricing.py
============================
Price change per class for the lines other than motor, from the rows step 20 kept. These lines have too few
claims for GLMs, so each class's loss ratio is blended with its department's by credibility.

  Experience   the last [lines] experience_years policy years up to the current year, skipping the class-years
               step 20 left out; written-year basis like motor (premium and claims of the policies written in
               the year)
  Claims       incurred (paid + case reserve). There are no development triangles for these lines, so recent
               years are not grossed up: their loss ratio is likely understated (shown as DEVELOPMENT)
  Large claims each department's claims above its LARGE_LOSS_QUANTILE are capped there; each class's own excess,
               over all its usable years, comes back as a load on its loss ratio
  Basis        gross of reinsurance (premium and claims, plan F2); the reinsurance cost is not an expense here,
               so the change is a gross technical indication until the manager decides gross or net
  Credibility  Z = min(1, sqrt(claims / [credibility] full_standard_claims)); class loss ratio blended with the
               department's. The department leans on its [lines] benchmark_loss_ratio when one is set, else it
               stands on its own experience and the price is marked low-confidence
  Price        indicated change = (credible loss ratio + commission ratio + [business] admin_expense_rate)
               / target combined ratio (dim_report_window, the same target as motor) - 1

A department with policies but no claims (step 20 "no claims data") gets its premium and nothing else.

Outputs: <facts>/fact_line_pricing.csv (one row per department x class),
         <facts>/fact_line_experience.csv (one row per department x class x policy year),
         <reports>/lines_pricing_report.md
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from settings import S, current_year

LARGE_LOSS_QUANTILE = 0.95     # method constant: few claims, so a lower quantile than motor's 99.5%
# Premium and claims are both gross of reinsurance (plan F2); the cost of reinsurance is not in the expenses, so the
# change is the gross technical indication, not a recommendation, until the manager decides the basis
BASIS = "gross of reinsurance; reinsurance cost not included"
CLASS = ["DEPARTMENT", "CLASS_DESC"]


def _log(m: str) -> None:
    print(f"[lines pricing] {m}", flush=True)


def settings() -> dict:
    lines, biz, cred = S.config.get("lines", {}), S.config.get("business", {}), S.config.get("credibility", {})
    window = S.dims_dir / "dim_report_window.csv"
    target = (float(pd.read_csv(window)["TARGET_COMBINED_RATIO"].iloc[0]) if window.exists()
              else float(biz["target_combined_ratio"]))
    return {"years": int(lines.get("experience_years", 5)), "benchmark": dict(lines.get("benchmark_loss_ratio", {})),
            "admin": float(biz["admin_expense_rate"]), "full": float(cred["full_standard_claims"]), "target": target}


def main() -> None:
    qpath = S.facts_dir / "fact_line_quality.csv"
    if not qpath.exists():
        _log("no fact_line_quality.csv (step 20 found no other-lines extract): nothing to do")
        return
    cfg = settings()
    _, cur = current_year()
    q = pd.read_csv(qpath)
    claims = pd.read_parquet(S.facts_dir / "fact_line_claim.parquet")
    first = cur - cfg["years"] + 1
    used = claims[(claims["EXCLUDE_REASON"] == "")].copy()

    # Large claims: cap per department on its usable claims; excess load over all its usable years
    cap = used.groupby("DEPARTMENT")["INCURRED"].quantile(LARGE_LOSS_QUANTILE).rename("CAP")
    used = used.join(cap, on="DEPARTMENT")
    used["CAPPED"] = used[["INCURRED", "CAP"]].min(axis=1)
    used["EXCESS"] = used["INCURRED"] - used["CAPPED"]
    # Each class carries its own large claims, spread over all its usable years (a long run, so one large
    # claim is not charged to the years it happened in, nor to another class of the department)
    yr = q[q["YEAR_STATUS"] == "used"]
    load = (used.groupby(CLASS)["EXCESS"].sum()
            / yr.groupby(CLASS)["PREMIUM_USED"].sum()).rename("EXCESS_LOAD")

    # Experience by class and year
    k = CLASS + ["POLICY_YEAR"]
    exp = (q[q["POLICY_YEAR"].between(first, cur)][k + ["YEAR_STATUS", "PREMIUM_USED", "COMMISSION_USED"]]
           .merge(used.groupby(k).agg(CLAIMS=("CLM_NO", "size"), INCURRED=("INCURRED", "sum"),
                                      CAPPED=("CAPPED", "sum")).reset_index(), on=k, how="left")
           .fillna({"CLAIMS": 0, "INCURRED": 0.0, "CAPPED": 0.0}))
    exp["USED"] = exp["YEAR_STATUS"] == "used"
    exp["LOSS_RATIO"] = np.where(exp["USED"] & (exp["PREMIUM_USED"] > 0), exp["INCURRED"] / exp["PREMIUM_USED"], np.nan)
    exp.to_csv(S.facts_dir / "fact_line_experience.csv", index=False, encoding="utf-8")

    e = exp[exp["USED"]]
    cls = e.groupby(CLASS).agg(FIRST_YEAR=("POLICY_YEAR", "min"), LAST_YEAR=("POLICY_YEAR", "max"),
                               N_YEARS=("POLICY_YEAR", "nunique"), PREMIUM=("PREMIUM_USED", "sum"),
                               COMMISSION=("COMMISSION_USED", "sum"), CLAIMS=("CLAIMS", "sum"),
                               INCURRED=("INCURRED", "sum"), CAPPED=("CAPPED", "sum")).reset_index()
    cls = cls.join(cap, on="DEPARTMENT").join(load, on=CLASS).fillna({"EXCESS_LOAD": 0.0})
    cls["LOSS_RATIO"] = np.where(cls["PREMIUM"] > 0, cls["CAPPED"] / cls["PREMIUM"] + cls["EXCESS_LOAD"], np.nan)
    cls["CREDIBILITY_Z"] = np.minimum(1.0, np.sqrt(cls["CLAIMS"] / cfg["full"]))

    # Department complement: its own experience, leaning on its benchmark when one is set
    cls["LOADED"] = cls["CAPPED"] + cls["EXCESS_LOAD"] * cls["PREMIUM"]
    dep = cls.groupby("DEPARTMENT").agg(D_PREMIUM=("PREMIUM", "sum"), D_LOADED=("LOADED", "sum"),
                                        D_CLAIMS=("CLAIMS", "sum"))
    dep["D_LR"] = dep["D_LOADED"] / dep["D_PREMIUM"].replace(0, np.nan)
    dep["D_Z"] = np.minimum(1.0, np.sqrt(dep["D_CLAIMS"] / cfg["full"]))
    bench = pd.Series(cfg["benchmark"], dtype=float)
    dep["BENCHMARK"] = bench.reindex(dep.index)
    dep["COMPLEMENT_LR"] = np.where(dep["BENCHMARK"].notna(),
                                    dep["D_Z"] * dep["D_LR"] + (1 - dep["D_Z"]) * dep["BENCHMARK"], dep["D_LR"])
    dep["COMPLEMENT_SOURCE"] = np.where(dep["BENCHMARK"].notna(), "department blended with its benchmark",
                                        "department experience only (no benchmark set)")
    cls = cls.join(dep[["COMPLEMENT_LR", "COMPLEMENT_SOURCE"]], on="DEPARTMENT")
    cls["CREDIBLE_LR"] = cls["CREDIBILITY_Z"] * cls["LOSS_RATIO"] + (1 - cls["CREDIBILITY_Z"]) * cls["COMPLEMENT_LR"]
    cls["COMMISSION_RATIO"] = cls["COMMISSION"] / cls["PREMIUM"].replace(0, np.nan)
    cls["ADMIN_RATIO"] = cfg["admin"]
    cls["COMBINED_RATIO"] = cls["CREDIBLE_LR"] + cls["COMMISSION_RATIO"] + cls["ADMIN_RATIO"]
    cls["TARGET_COMBINED_RATIO"] = cfg["target"]
    cls["INDICATED_CHANGE"] = cls["COMBINED_RATIO"] / cfg["target"] - 1
    cls["STATUS"] = np.where(cls["CREDIBILITY_Z"] >= 1, "full credibility",
                             np.where(cls["COMPLEMENT_SOURCE"].str.startswith("department experience"),
                                      "low confidence: no benchmark", "partly credible"))
    cls["DEVELOPMENT"] = "incurred to date, not developed (no triangles for these lines)"
    cls["BASIS"] = BASIS

    # Every class in the extract gets a row; those with nothing usable say why, with empty figures
    allc = q.groupby(CLASS).agg(PREMIUM_ALL=("PREMIUM", "sum"), STATUSES=("YEAR_STATUS", lambda s: set(s))).reset_index()
    out = allc.merge(cls, on=CLASS, how="left")
    nothing = out["PREMIUM"].isna()
    out.loc[nothing, "STATUS"] = np.where(out.loc[nothing, "STATUSES"].map(lambda s: "no claims data" in s),
                                          "no claims data", f"no usable years in {first}-{cur}")
    out["YEARS_USED"] = np.where(out["N_YEARS"].notna(),
                                 out["FIRST_YEAR"].astype("Int64").astype(str) + "-" + out["LAST_YEAR"].astype("Int64").astype(str)
                                 + " (" + out["N_YEARS"].astype("Int64").astype(str) + " years)", "")
    cols = CLASS + ["STATUS", "YEARS_USED", "N_YEARS", "PREMIUM_ALL", "PREMIUM", "CLAIMS", "INCURRED", "CAPPED", "CAP",
                    "EXCESS_LOAD", "LOSS_RATIO", "CREDIBILITY_Z", "COMPLEMENT_LR", "COMPLEMENT_SOURCE", "CREDIBLE_LR",
                    "COMMISSION_RATIO", "ADMIN_RATIO", "COMBINED_RATIO", "TARGET_COMBINED_RATIO", "INDICATED_CHANGE",
                    "DEVELOPMENT", "BASIS"]
    out = out[cols].rename(columns={"CAPPED": "INCURRED_CAPPED", "CAP": "LARGE_LOSS_CAP"}).sort_values(CLASS)
    out.to_csv(S.facts_dir / "fact_line_pricing.csv", index=False, encoding="utf-8")

    S.reports_dir.mkdir(parents=True, exist_ok=True)
    with open(S.reports_dir / "lines_pricing_report.md", "w", encoding="utf-8") as f:
        f.write(f"# Other lines: indicated price change\n\nExperience {first}-{cur} (written year), target combined ratio "
                f"{cfg['target']:.0%}, admin {cfg['admin']:.0%}, full credibility at {cfg['full']:,.0f} claims. Claims are "
                f"incurred to date, not developed. **Basis: {BASIS}**: the change is an indication, not a "
                "recommendation, until gross or net pricing is decided.\n\n| Department | Class | Status | Years | Premium | Claims | Loss ratio | "
                "Z | Credible LR | Combined | Change |\n|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|\n")
        for r in out.itertuples():
            pct = lambda v: "" if pd.isna(v) else f"{v:.1%}"
            f.write(f"| {r.DEPARTMENT} | {r.CLASS_DESC} | {r.STATUS} | {r.YEARS_USED} | "
                    f"{'' if pd.isna(r.PREMIUM) else f'{r.PREMIUM / 1e6:,.1f}M'} | "
                    f"{'' if pd.isna(r.CLAIMS) else f'{r.CLAIMS:,.0f}'} | {pct(r.LOSS_RATIO)} | "
                    f"{'' if pd.isna(r.CREDIBILITY_Z) else f'{r.CREDIBILITY_Z:.2f}'} | {pct(r.CREDIBLE_LR)} | "
                    f"{pct(r.COMBINED_RATIO)} | {'' if pd.isna(r.INDICATED_CHANGE) else f'{r.INDICATED_CHANGE:+.1%}'} |\n")

    _log(f"{len(out)} classes; experience {first}-{cur}; target {cfg['target']:.0%}")
    for r in out.itertuples():
        if pd.isna(r.INDICATED_CHANGE):
            _log(f"  {r.DEPARTMENT} / {r.CLASS_DESC}: {r.STATUS}")
        else:
            _log(f"  {r.DEPARTMENT} / {r.CLASS_DESC}: {r.YEARS_USED}, premium {r.PREMIUM / 1e6:,.1f}M, {r.CLAIMS:,.0f} claims, "
                 f"LR {r.LOSS_RATIO:.1%}, Z {r.CREDIBILITY_Z:.2f} -> credible {r.CREDIBLE_LR:.1%}, combined "
                 f"{r.COMBINED_RATIO:.1%}, change {r.INDICATED_CHANGE:+.1%} ({r.STATUS})")


if __name__ == "__main__":
    main()
