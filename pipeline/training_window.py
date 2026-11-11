"""
pipeline/training_window.py
===========================
How many years of history each model learns from, chosen per company from its own data.

The extract holds the company's whole history ([oracle] first_policy_year = 0). Old years can be a
different book (another channel mix, a system migration, a gap), and a model trained on them can
forecast today worse. So each model tries a few windows on its rolling backtest, scored on the
latest forecast origins, and keeps the shortest window unless a longer one is clearly better.
Power BI shows only the last [report] years whatever the models learn from.

  Candidates     CANDIDATE_YEARS (method constant); a window longer than the data is skipped
  Score          each candidate's error at every test origin (the same origins for all)
  Rule           one-standard-error rule: find the lowest mean error; every window within one
                 standard error of it is as good as the test can tell, and of those the LONGEST is
                 kept, because more data gives steadier estimates. A shorter window wins only when
                 it is clearly better, which is what happens when old years are a different book.
  Override       [training] window_years in config.toml: "auto" (default), a number of years, or "all"
  Record         fact_training_window.csv: one row per model x candidate with its error, its
                 standard error and the choice
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from settings import S

CANDIDATE_YEARS: tuple[int | None, ...] = (3, 5, 7, None)   # None = all history
RECORD = "fact_training_window.csv"


def label(years: int | None) -> str:
    return "all" if years is None else f"{years} years"


def configured() -> int | None | str:
    """The company setting: "auto", a number of years, or None for all history."""
    v = S.config.get("training", {}).get("window_years", "auto")
    if isinstance(v, str) and v.lower() in ("auto", "all"):
        return "auto" if v.lower() == "auto" else None
    if isinstance(v, int) and v >= 1:
        return v
    raise SystemExit('[training] window_years in config.toml must be "auto", "all" or a whole number of years.')


def candidates(years_of_data: float) -> list[int | None]:
    """Candidate windows the data can support (a window as long as the data equals "all")."""
    return [w for w in CANDIDATE_YEARS if w is None or w < years_of_data]


def _summary(errs) -> tuple[float, float, int]:
    """Mean error, its standard error and the number of origins it rests on."""
    e = np.asarray([x for x in np.atleast_1d(errs) if pd.notna(x)], dtype=float)
    if len(e) == 0:
        return np.nan, np.nan, 0
    se = e.std(ddof=1) / np.sqrt(len(e)) if len(e) > 1 else 0.0
    return float(e.mean()), float(se), len(e)


def choose(errors: dict) -> tuple[int | None, str]:
    """One-standard-error rule. `errors` maps window -> per-origin errors (NaN = not tested).
    Returns (window, reason)."""
    stats = {w: _summary(e) for w, e in errors.items()}
    tested = {w: s for w, s in stats.items() if pd.notna(s[0])}
    if not tested:
        return None, "no window could be tested: all history used"
    best = min(tested, key=lambda w: tested[w][0])
    limit = tested[best][0] + tested[best][1]
    close = [w for w, s in tested.items() if s[0] <= limit]
    longest = max(close, key=lambda w: float("inf") if w is None else w)
    if longest == best:
        why = "lowest error" + ("" if len(close) == 1 else ", and the longest within one standard error")
    else:
        why = f"longest window within one standard error of the best ({label(best)})"
    return longest, why


def resolve(model: str, errors: dict, metric: str) -> int | None:
    """Apply the company override or the rule, record the decision and return the window.
    `errors` maps window -> per-origin errors."""
    setting = configured()
    if setting == "auto":
        window, why = choose(errors)
    else:
        window, why = setting, "set in config.toml [training] window_years"
    rows = []
    for w, e in errors.items():
        mean, se, n = _summary(e)
        rows.append({"MODEL": model, "WINDOW": label(w), "WINDOW_YEARS": w, "ERROR": mean, "STD_ERROR": se,
                     "METRIC": metric, "N_ORIGINS": n, "CHOSEN": w == window, "REASON": why if w == window else ""})
    if window not in errors:
        rows.append({"MODEL": model, "WINDOW": label(window), "WINDOW_YEARS": window, "ERROR": np.nan,
                     "STD_ERROR": np.nan, "METRIC": metric, "N_ORIGINS": 0, "CHOSEN": True, "REASON": why})
    path = S.facts_dir / RECORD
    old = pd.read_csv(path) if path.exists() else pd.DataFrame()
    keep = old[old["MODEL"] != model] if len(old) else old
    pd.concat([keep, pd.DataFrame(rows)], ignore_index=True).to_csv(path, index=False, encoding="utf-8")
    shown = ", ".join(f"{r['WINDOW']} {r['ERROR']:.1%} ±{r['STD_ERROR']:.1%}" for r in rows if pd.notna(r["ERROR"]))
    print(f"[training window] {model}: {label(window)} ({why}); {metric}: {shown}", flush=True)
    return window


def chosen(model: str) -> int | None:
    """The window an earlier step chose for `model` (all history if none was recorded)."""
    path = S.facts_dir / RECORD
    if not path.exists():
        return None
    r = pd.read_csv(path)
    r = r[(r["MODEL"] == model) & r["CHOSEN"]]
    if r.empty or pd.isna(r["WINDOW_YEARS"].iloc[0]):
        return None
    return int(r["WINDOW_YEARS"].iloc[0])
