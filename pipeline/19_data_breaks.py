"""
pipeline/19_data_breaks.py
==========================
Structural breaks in the company's own data, so every automatic choice comes with a reason and a new
extract's problems show early (a gap, a broken key, a channel change). Report only: nothing else reads it.

  Volume gaps      a month with under GAP_SHARE of its trailing 12-month median policy count
  Volume jumps     policies or premium changing by JUMP_SHARE or more on the year before
  Channel shifts   a sales channel's share of premium moving CHANNEL_PTS or more in a year
  Mix shifts       a class or policy type's share of premium moving MIX_PTS or more in a year
  Claims / policy  claims per 1,000 policies changing by JUMP_SHARE or more (a broken key or a missing load)
  After cut-off    policy years beyond the claims data's current year

Only complete years up to the current year (settings.current_year) are compared, so the year the data
stops in is not reported as a collapse. The report links the breaks to the training windows the models
chose (fact_training_window.csv).

Output: <facts>/fact_data_breaks.csv, <reports>/data_breaks_report.md
"""
from __future__ import annotations

import pandas as pd

from settings import S, current_year

# Method constants: how big a change must be to be reported
GAP_SHARE = 0.25
JUMP_SHARE = 0.35
CHANNEL_PTS = 20
MIX_PTS = 10


def _log(m: str) -> None:
    print(f"[data breaks] {m}", flush=True)


def year_changes(p: pd.DataFrame, claims: pd.Series, last: int) -> list[dict]:
    y = p[p["POLICY_YEAR"] <= last].groupby("POLICY_YEAR").agg(POLICIES=("TOTAL_PREMIUM", "size"),
                                                               PREMIUM=("TOTAL_PREMIUM", "sum"))
    y["CLAIMS_PER_1000"] = claims.reindex(y.index).fillna(0) / y["POLICIES"] * 1000
    rows = []
    for col, label in (("POLICIES", "policies"), ("PREMIUM", "premium"), ("CLAIMS_PER_1000", "claims per 1,000 policies")):
        chg = y[col].pct_change()
        for year, c in chg.items():
            if pd.notna(c) and abs(c) >= JUMP_SHARE:
                kind = "Claims per policy" if col == "CLAIMS_PER_1000" else "Volume jump"
                rows.append({"TYPE": kind, "PERIOD": str(year), "MEASURE": label, "BEFORE": y[col].shift(1)[year],
                             "AFTER": y[col][year], "CHANGE": c,
                             "NOTE": f"{label} {c:+.0%} on {year - 1}"})
    return rows


def share_shifts(p: pd.DataFrame, by: str, kind: str, pts: float, last: int) -> list[dict]:
    s = pd.crosstab(p.loc[p["POLICY_YEAR"] <= last, "POLICY_YEAR"], p.loc[p["POLICY_YEAR"] <= last, by],
                    values=p.loc[p["POLICY_YEAR"] <= last, "TOTAL_PREMIUM"], aggfunc="sum", normalize="index") * 100
    d = s.diff()
    rows = []
    for year in d.index[1:]:
        for name, c in d.loc[year].items():
            if abs(c) >= pts:
                rows.append({"TYPE": kind, "PERIOD": str(year), "MEASURE": f"{by} = {name}",
                             "BEFORE": s.loc[year - 1, name], "AFTER": s.loc[year, name], "CHANGE": c / 100,
                             "NOTE": f"{name}: {s.loc[year - 1, name]:.0f}% -> {s.loc[year, name]:.0f}% of premium"})
    return rows


def gaps(p: pd.DataFrame, cutoff: pd.Timestamp) -> list[dict]:
    m = p[p["POLICY_EFFECTIVE_DATE"] <= cutoff].groupby(p["POLICY_EFFECTIVE_DATE"].dt.to_period("M")).size()
    m = m.reindex(pd.period_range(m.index.min(), cutoff.to_period("M") - 1, freq="M"), fill_value=0)
    med = m.rolling(12, min_periods=6).median().shift(1)
    low = m[m < GAP_SHARE * med]
    rows, run = [], []
    for month in list(low.index) + [None]:                 # group consecutive months into one finding
        if run and (month is None or month != run[-1] + 1):
            rows.append({"TYPE": "Volume gap", "PERIOD": f"{run[0]} to {run[-1]}" if len(run) > 1 else str(run[0]),
                         "MEASURE": "policies per month", "BEFORE": float(med[run[0]]), "AFTER": float(m[run].mean()),
                         "CHANGE": float(m[run].mean() / med[run[0]] - 1),
                         "NOTE": f"{len(run)} month(s) at {m[run].mean():,.0f} policies against a usual {med[run[0]]:,.0f}"})
            run = []
        if month is not None:
            run.append(month)
    return rows


def main() -> None:
    cutoff, cur = current_year()
    p = pd.read_csv(S.facts_dir / "fact_policy.csv",
                    usecols=["POLICY_YEAR", "POLICY_EFFECTIVE_DATE", "TOTAL_PREMIUM", "SOURCE_DESC", "CLASS_DESC",
                             "POLICY_TYPE_DESC"], parse_dates=["POLICY_EFFECTIVE_DATE"], low_memory=False)
    c = pd.read_csv(S.facts_dir / "fact_claim.csv", usecols=["POLICY_YEAR", "POLICY_MATCHED"], low_memory=False)
    claims = c[c["POLICY_MATCHED"].astype(str).str.lower() == "true"].groupby("POLICY_YEAR").size()

    rows = gaps(p, cutoff) + year_changes(p, claims, cur)
    rows += share_shifts(p, "SOURCE_DESC", "Channel shift", CHANNEL_PTS, cur)
    rows += share_shifts(p, "CLASS_DESC", "Mix shift", MIX_PTS, cur)
    rows += share_shifts(p, "POLICY_TYPE_DESC", "Mix shift", MIX_PTS, cur)
    after = p[p["POLICY_YEAR"] > cur].groupby("POLICY_YEAR").size()
    for year, n in after.items():
        rows.append({"TYPE": "After cut-off", "PERIOD": str(year), "MEASURE": "policies", "BEFORE": None, "AFTER": n,
                     "CHANGE": None, "NOTE": f"{n:,} policies in policy year {year}, after the claims data's "
                                         f"current year {cur} (claims to {cutoff.date()}): not used as a full year"})
    out = pd.DataFrame(rows, columns=["TYPE", "PERIOD", "MEASURE", "BEFORE", "AFTER", "CHANGE", "NOTE"])
    out["FIRST_YEAR"] = out["PERIOD"].str[:4].astype(int)
    out = out.sort_values(["FIRST_YEAR", "TYPE"]).drop(columns="FIRST_YEAR").reset_index(drop=True)
    out.to_csv(S.facts_dir / "fact_data_breaks.csv", index=False, encoding="utf-8")

    in_book = out[out["TYPE"] != "After cut-off"]
    latest = int(in_book["PERIOD"].str[:4].astype(int).max()) if len(in_book) else None
    windows = S.facts_dir / "fact_training_window.csv"
    chosen = pd.read_csv(windows) if windows.exists() else pd.DataFrame()
    chosen = chosen[chosen["CHOSEN"]] if len(chosen) else chosen

    S.reports_dir.mkdir(parents=True, exist_ok=True)
    with open(S.reports_dir / "data_breaks_report.md", "w", encoding="utf-8") as f:
        f.write("# Changes found in the data\n\n")
        f.write(f"Policy years to {cur} (claims to {cutoff.date()}). A change is reported when volume moves by "
                f"{JUMP_SHARE:.0%} or more in a year, a month falls under {GAP_SHARE:.0%} of its usual volume, a channel's "
                f"share moves {CHANNEL_PTS} points or a class / policy type's {MIX_PTS} points.\n\n")
        if latest:
            f.write(f"**Latest change: {latest}.** Models that learn from years before it may learn a different book.\n\n")
        for _, r in chosen.iterrows():
            f.write(f"- {r['MODEL']}: trained on {r['WINDOW']} ({r['REASON']}).\n")
        f.write("\n| Type | Period | What | Change |\n|---|---|---|---|\n")
        for r in out.itertuples():
            f.write(f"| {r.TYPE} | {r.PERIOD} | {r.MEASURE} | {r.NOTE} |\n")

    _log(f"{len(out)} changes found; latest in the book: {latest}; written fact_data_breaks.csv and data_breaks_report.md")
    for r in out.itertuples():
        print(f"  {r.PERIOD:<20} {r.TYPE:<17} {r.NOTE}")


if __name__ == "__main__":
    main()
