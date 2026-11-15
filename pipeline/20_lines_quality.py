"""
pipeline/20_lines_quality.py
============================
Data-quality rules for the lines of business other than motor (sql/lines_policies.sql, sql/lines_claims.sql),
the same for every company's data. Nothing is fixed by hand: each rule flags rows or class-years, they are
left out with the reason, and the tables say what was used and what was not. Pricing (step 21) uses only
what passes.

Policies, in this order (the first rule a row fails is its reason):
  after cut-off        policy year after the data's current year (settings.current_year)
  impossible dates     no dates, cover ending before it starts, over MAX_TERM_DAYS, or a start more than a year
                       from its own policy year (the same rule as motor, 01_prepare_real.py)
  inconsistent amounts gross premium more than MAX_RATIO times the total premium, or under 1 / MAX_RATIO
Class-years:
  possibly incomplete  a class-year whose policy count and premium both fall under (1 - INCOMPLETE_DROP) x
                       the median of its PRIOR_YEARS earlier years, or whose claims (MIN_CLAIMS or more) mostly
                       find no policy (over UNMATCHED_LIMIT): records are missing; its loss ratio would mislead
  very large (kept)    premium or sum insured over 10 ** EXTREME_DECADES x its class median: flagged to check
  no claims data       a department with policies but no claims in the extract (e.g. claims kept elsewhere):
                       premium is shown, loss ratio and price stay empty
Claims:
  matched by the 7-part policy key and policy year, else by policy number + year + class when exactly one
  policy has them, else unmatched. A claim follows its policy's exclusion, else its own class-year's.
  Unmatched claims still count in their own class-year: the loss ratio is by class and year, not by policy.
  Incurred = paid + case reserve (the estimate when larger than paid), as motor.

Outputs: <facts>/fact_line_quality.csv (one row per department x class x policy year),
         <facts>/fact_line_policy.parquet, <facts>/fact_line_claim.parquet (rows with EXCLUDE_REASON),
         <reports>/lines_quality_report.md
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from settings import S, current_year

# Method constants
MAX_TERM_DAYS = 2 * 366
MAX_RATIO = 100            # gross premium vs total premium
EXTREME_DECADES = 4        # flagged for checking (kept): over 10,000 x its class's median
INCOMPLETE_DROP = 0.5
PRIOR_YEARS = 3
MIN_CLAIMS = 5             # a class-year needs this many claims before its match rate means anything
UNMATCHED_LIMIT = 0.2      # more unmatched claims than this: the class-year's policies are missing
DATE_RANGE = ("1900-01-01", "2100-12-31")
AMOUNTS = ["TOTAL_PREMIUM", "GROSS_PREMIUM", "NET_PREMIUM", "SUM_INSURED"]
CLASS = ["DEPARTMENT", "CLASS_DESC"]


def _log(m: str) -> None:
    print(f"[lines quality] {m}", flush=True)


def _date(s: pd.Series) -> pd.Series:
    """Dates outside DATE_RANGE are typing errors (e.g. year 1019): missing, so the date rule catches them."""
    if not pd.api.types.is_datetime64_any_dtype(s):
        s = pd.to_datetime(s, errors="coerce")
    s = s.where((s >= pd.Timestamp(DATE_RANGE[0])) & (s <= pd.Timestamp(DATE_RANGE[1])))
    return s.astype("datetime64[ns]")


def load() -> tuple[pd.DataFrame, pd.DataFrame] | None:
    paths = [S.raw_dir / f"{n}.parquet" for n in ("lines_policies", "lines_claims")] if S.raw_dir else []
    if not paths or not all(p.exists() for p in paths):
        _log("no other-lines extract (sql/lines_policies.sql, lines_claims.sql): nothing to do")
        return None
    p, c = (pd.read_parquet(x) for x in paths)
    for df, cols in ((p, ("POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE")), (c, ("ACC_DATE", "CLOSE_DATE"))):
        for col in cols:
            df[col] = _date(df[col])
    for col in AMOUNTS + ["COMMISSION"]:
        p[col] = pd.to_numeric(p[col], errors="coerce")
    for col in ("TOTAL_EST", "TOTAL_PAID", "RECOVERY_AMT"):
        c[col] = pd.to_numeric(c[col], errors="coerce").fillna(0.0)
    return p, c


def policy_rules(p: pd.DataFrame, cur: int) -> pd.Series:
    reason = pd.Series("", index=p.index, dtype=object)

    def mark(mask: pd.Series, why: str) -> None:
        reason.loc[mask & (reason == "")] = why

    mark(p["POLICY_YEAR"] > cur, "after cut-off")
    term = (p["POLICY_EXPIRY_DATE"] - p["POLICY_EFFECTIVE_DATE"]).dt.days
    start_year = p["POLICY_EFFECTIVE_DATE"].dt.year
    mark(term.isna() | (term <= 0) | (term > MAX_TERM_DAYS)
         | ~start_year.between(p["POLICY_YEAR"] - 1, p["POLICY_YEAR"] + 1), "impossible dates")
    # Amounts that contradict each other: gross premium more than MAX_RATIO times the total, or under
    # 1 / MAX_RATIO of it (a typing or unit error; a real difference is far smaller)
    r = p["GROSS_PREMIUM"].where(p["GROSS_PREMIUM"] > 0) / p["TOTAL_PREMIUM"].where(p["TOTAL_PREMIUM"] > 0)
    mark(r.notna() & ((r > MAX_RATIO) | (r < 1 / MAX_RATIO)), "inconsistent amounts")
    return reason


def very_large(p: pd.DataFrame) -> pd.Series:
    """Flag only (kept in): premium or sum insured over 10 ** EXTREME_DECADES times its class's median among
    the rows used. A real large risk and a typing error look alike here, so the row is shown for checking."""
    ok = p["EXCLUDE_REASON"] == ""
    flag = pd.Series(False, index=p.index)
    for col in AMOUNTS:
        x = p[col].where(p[col] > 0)
        med = x[ok].groupby([p.loc[ok, c] for c in CLASS]).median().rename("MED").reset_index()
        flag |= (x > p[CLASS].merge(med, on=CLASS, how="left")["MED"].to_numpy() * 10 ** EXTREME_DECADES).fillna(False)
    return flag & ok


def year_status(p: pd.DataFrame, c: pd.DataFrame) -> pd.DataFrame:
    """Status of each department x class x policy year among the rows that passed the policy rules. Possibly
    incomplete when (a) both its policy count and its premium fall under (1 - INCOMPLETE_DROP) x the median of
    its PRIOR_YEARS earlier years (records missing or the class collapsing; a fall in one of them alone is a
    real change of mix), or (b) at least MIN_CLAIMS of its claims came in and over UNMATCHED_LIMIT of them find
    no policy (the policies are missing from the extract)."""
    used = p[p["EXCLUDE_REASON"] == ""]
    n = used.groupby(CLASS + ["POLICY_YEAR"]).agg(N=("POLICY_NO", "size"), PREM=("TOTAL_PREMIUM", "sum")).reset_index()
    cl = c.groupby(CLASS + ["POLICY_YEAR"]).agg(CLAIMS=("CLM_NO", "size"),
                                               UNMATCHED=("MATCH", lambda s: (s == "unmatched").sum()))
    n = n.merge(cl.reset_index(), on=CLASS + ["POLICY_YEAR"], how="left").fillna({"CLAIMS": 0, "UNMATCHED": 0})
    rows = []
    for _, g in n.groupby(CLASS):
        g = g.sort_values("POLICY_YEAR")
        for i, r in enumerate(g.itertuples()):
            prior = g.iloc[max(0, i - PRIOR_YEARS):i]
            drop = (len(prior) > 0 and r.N < (1 - INCOMPLETE_DROP) * prior["N"].median()
                    and r.PREM < (1 - INCOMPLETE_DROP) * prior["PREM"].median())
            missing = r.CLAIMS >= MIN_CLAIMS and r.UNMATCHED / r.CLAIMS > UNMATCHED_LIMIT
            why = ("possibly incomplete" if drop or missing else "used")
            rows.append({"DEPARTMENT": r.DEPARTMENT, "CLASS_DESC": r.CLASS_DESC, "POLICY_YEAR": r.POLICY_YEAR,
                         "YEAR_STATUS": why, "INCOMPLETE_SIGN": "; ".join(
                             x for x, on in (("volume and premium fell", drop), ("claims without policies", missing)) if on),
                         "PRIOR_MEDIAN_POLICIES": float(prior["N"].median()) if len(prior) else np.nan})
    s = pd.DataFrame(rows, columns=CLASS + ["POLICY_YEAR", "YEAR_STATUS", "INCOMPLETE_SIGN", "PRIOR_MEDIAN_POLICIES"])
    no_claims = set(p["DEPARTMENT"].unique()) - set(c["DEPARTMENT"].unique())
    s.loc[s["DEPARTMENT"].isin(no_claims) & (s["YEAR_STATUS"] == "used"), "YEAR_STATUS"] = "no claims data"
    return s


def match_claims(c: pd.DataFrame, p: pd.DataFrame) -> pd.DataFrame:
    key = p[["POLICY_NO", "POLICY_YEAR", "EXCLUDE_REASON"]].drop_duplicates(["POLICY_NO", "POLICY_YEAR"])
    c = c.merge(key.rename(columns={"EXCLUDE_REASON": "POLICY_REASON"}), on=["POLICY_NO", "POLICY_YEAR"],
                how="left", indicator=True)
    c["MATCH"] = np.where(c.pop("_merge") == "both", "key", "")
    alt = p.groupby(["POLICY_NUMBER", "POLICY_YEAR", "CLASS_NO"])["EXCLUDE_REASON"].agg(["size", "first"])
    alt = alt[alt["size"] == 1]["first"].rename("ALT_REASON").reset_index()     # exactly one policy only
    c = c.merge(alt, on=["POLICY_NUMBER", "POLICY_YEAR", "CLASS_NO"], how="left")
    second = (c["MATCH"] == "") & c["ALT_REASON"].notna()
    c.loc[second, "MATCH"] = "number + year + class"
    c.loc[second, "POLICY_REASON"] = c.loc[second, "ALT_REASON"]
    c.loc[c["MATCH"] == "", "MATCH"] = "unmatched"
    c["INCURRED"] = c[["TOTAL_EST", "TOTAL_PAID"]].max(axis=1)
    return c.drop(columns="ALT_REASON")


def main() -> None:
    data = load()
    if data is None:
        return
    p, c = data
    cutoff, cur = current_year()
    p["EXCLUDE_REASON"] = policy_rules(p, cur)
    c = match_claims(c, p)
    status = year_status(p, c)
    p = p.merge(status[CLASS + ["POLICY_YEAR", "YEAR_STATUS"]], on=CLASS + ["POLICY_YEAR"], how="left")
    bad_year = p["YEAR_STATUS"].eq("possibly incomplete") & (p["EXCLUDE_REASON"] == "")
    p.loc[bad_year, "EXCLUDE_REASON"] = "possibly incomplete"
    p["VERY_LARGE"] = very_large(p)

    c = c.merge(status[CLASS + ["POLICY_YEAR", "YEAR_STATUS"]], on=CLASS + ["POLICY_YEAR"], how="left")
    c["EXCLUDE_REASON"] = c["POLICY_REASON"].fillna("")
    own = c["EXCLUDE_REASON"] == ""
    c.loc[own & (c["POLICY_YEAR"] > cur), "EXCLUDE_REASON"] = "after cut-off"
    c.loc[own & c["YEAR_STATUS"].eq("possibly incomplete"), "EXCLUDE_REASON"] = "possibly incomplete"
    c.loc[own & c["YEAR_STATUS"].isna() & (c["POLICY_YEAR"] <= cur), "EXCLUDE_REASON"] = "no policies in class-year"

    # One row per department x class x policy year: everything extracted = used + left out
    k = CLASS + ["POLICY_YEAR"]
    pu, cu = p["EXCLUDE_REASON"] == "", c["EXCLUDE_REASON"] == ""
    q = (p.groupby(k).agg(POLICIES=("POLICY_NO", "size"), PREMIUM=("TOTAL_PREMIUM", "sum"))
         .join(p[pu].groupby(k).agg(POLICIES_USED=("POLICY_NO", "size"), PREMIUM_USED=("TOTAL_PREMIUM", "sum"),
                                    COMMISSION_USED=("COMMISSION", "sum"), CHECK_VERY_LARGE=("VERY_LARGE", "sum")))
         .join(p[~pu].groupby(k)["EXCLUDE_REASON"].agg(lambda s: "; ".join(f"{r} {n:,}" for r, n in s.value_counts().items()))
               .rename("POLICIES_LEFT_OUT"))
         .join(c.groupby(k).agg(CLAIMS=("CLM_NO", "size"), INCURRED=("INCURRED", "sum"), PAID=("TOTAL_PAID", "sum")),
               how="outer")
         .join(c[cu].groupby(k).agg(CLAIMS_USED=("CLM_NO", "size"), INCURRED_USED=("INCURRED", "sum")))
         .join(c.pivot_table(index=k, columns="MATCH", values="CLM_NO", aggfunc="size")
               .rename(columns=lambda m: "MATCHED_" + m.upper().replace(" + ", "_").replace(" ", "_")))
         .reset_index().merge(status, on=k, how="left"))
    for col in ("POLICIES", "PREMIUM", "POLICIES_USED", "PREMIUM_USED", "COMMISSION_USED", "CLAIMS", "INCURRED", "PAID",
                "CLAIMS_USED", "INCURRED_USED", "MATCHED_KEY", "MATCHED_NUMBER_YEAR_CLASS", "MATCHED_UNMATCHED",
                "CHECK_VERY_LARGE"):
        if col not in q:
            q[col] = 0.0
        q[col] = q[col].fillna(0.0)
    q["POLICIES_LEFT_OUT"] = q["POLICIES_LEFT_OUT"].fillna("")
    q["INCOMPLETE_SIGN"] = q["INCOMPLETE_SIGN"].fillna("")
    q.loc[q["POLICY_YEAR"] > cur, "YEAR_STATUS"] = "after cut-off"
    q["YEAR_STATUS"] = q["YEAR_STATUS"].fillna("no policies in class-year")
    cols = k + ["YEAR_STATUS", "POLICIES", "POLICIES_USED", "PREMIUM", "PREMIUM_USED", "COMMISSION_USED",
                "POLICIES_LEFT_OUT", "CLAIMS", "CLAIMS_USED", "INCURRED", "INCURRED_USED", "PAID", "MATCHED_KEY",
                "MATCHED_NUMBER_YEAR_CLASS", "MATCHED_UNMATCHED", "PRIOR_MEDIAN_POLICIES", "INCOMPLETE_SIGN",
                "CHECK_VERY_LARGE"]
    q = q[cols].sort_values(k).reset_index(drop=True)
    q.to_csv(S.facts_dir / "fact_line_quality.csv", index=False, encoding="utf-8")
    p.to_parquet(S.facts_dir / "fact_line_policy.parquet", index=False)
    c.to_parquet(S.facts_dir / "fact_line_claim.parquet", index=False)

    # Report
    by = q.groupby("DEPARTMENT").agg(PREMIUM=("PREMIUM", "sum"), PREMIUM_USED=("PREMIUM_USED", "sum"),
                                     CLAIMS=("CLAIMS", "sum"), CLAIMS_USED=("CLAIMS_USED", "sum"),
                                     KEY=("MATCHED_KEY", "sum"), SECOND=("MATCHED_NUMBER_YEAR_CLASS", "sum"))
    reasons = p.loc[~pu].groupby("EXCLUDE_REASON").agg(N=("POLICY_NO", "size"), PREMIUM=("TOTAL_PREMIUM", "sum"))
    years = q[q["YEAR_STATUS"] == "possibly incomplete"]
    S.reports_dir.mkdir(parents=True, exist_ok=True)
    with open(S.reports_dir / "lines_quality_report.md", "w", encoding="utf-8") as f:
        f.write(f"# Other lines: data quality\n\nPolicy years to {cur} (data to {cutoff.date()}). Rules: see "
                "`pipeline/20_lines_quality.py`.\n\n| Department | Premium | Used | Claims | Used | Matched by key | "
                "by number + year + class |\n|---|---:|---:|---:|---:|---:|---:|\n")
        for d, r in by.iterrows():
            f.write(f"| {d} | {r.PREMIUM / 1e6:,.1f}M | {r.PREMIUM_USED / 1e6:,.1f}M | {r.CLAIMS:,.0f} | "
                    f"{r.CLAIMS_USED:,.0f} | {r.KEY / max(r.CLAIMS, 1):.1%} | {r.SECOND / max(r.CLAIMS, 1):.1%} |\n")
        f.write("\n## Policies left out\n\n| Reason | Policies | Premium |\n|---|---:|---:|\n")
        for why, r in reasons.iterrows():
            f.write(f"| {why} | {r.N:,} | {r.PREMIUM / 1e6:,.1f}M |\n")
        f.write("\n## Class-years left out as possibly incomplete\n\n| Department | Class | Year | Policies | "
                f"Median of the {PRIOR_YEARS} years before | Sign |\n|---|---|---:|---:|---:|---|\n")
        for r in years.itertuples():
            f.write(f"| {r.DEPARTMENT} | {r.CLASS_DESC} | {r.POLICY_YEAR} | {r.POLICIES:,.0f} | {r.PRIOR_MEDIAN_POLICIES:,.0f} | "
                    f"{r.INCOMPLETE_SIGN} |\n")
        nc = sorted(q.loc[q["YEAR_STATUS"] == "no claims data", "DEPARTMENT"].unique())
        if nc:
            f.write(f"\n**No claims data:** {', '.join(nc)}: premium shown, no loss ratio or price.\n")

    _log(f"{len(p):,} policy-years, {len(c):,} claims; premium used {q['PREMIUM_USED'].sum() / 1e6:,.1f}M of "
         f"{q['PREMIUM'].sum() / 1e6:,.1f}M, claims used {int(q['CLAIMS_USED'].sum()):,} of {int(q['CLAIMS'].sum()):,}")
    for why, r in reasons.iterrows():
        _log(f"  left out, {why}: {r.N:,} policies, {r.PREMIUM / 1e6:,.1f}M")
    for r in years.itertuples():
        _log(f"  possibly incomplete: {r.DEPARTMENT} {r.CLASS_DESC} {r.POLICY_YEAR}: {r.INCOMPLETE_SIGN} "
             f"({r.POLICIES:,.0f} policies vs a usual {r.PRIOR_MEDIAN_POLICIES:,.0f}; {r.MATCHED_UNMATCHED:,.0f} of "
             f"{r.CLAIMS:,.0f} claims unmatched)")
    _log(f"  flagged for checking (kept): {int(q['CHECK_VERY_LARGE'].sum()):,} very large policies")
    m = c["MATCH"].value_counts(normalize=True)
    _log("  claims matched: " + ", ".join(f"{k} {v:.1%}" for k, v in m.items()))


if __name__ == "__main__":
    sys.exit(main())
