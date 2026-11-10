"""
pipeline/08_forecast_kpi_cards.py
===================================
Writes dim_metric.csv: display names, formats and colour direction of the six forecast
metrics, read by the Power BI Forecast page.

The Forecast page's figures (chart and cards) come from one source only, step 07
(fact_forecast_seasonal). This step used to build its own flat run-rate forecast
(fact_kpi_forecast, fact_timeseries) from the pricing GLMs, with its own dates and backtest
constants typed into the code. Its figures agreed with nothing on the page and no visual reads
them (docs/MODEL_CLEANUP.md), so it was retired on 2026-11-10 (docs/MODEL_IMPROVEMENT_PLAN.md,
A3) and the old files are removed when this step runs. The old code is in git history.

Output (to 02_facts/):
  dim_metric.csv   six rows of display metadata. Power BI reads all six columns, so keep them.
"""

from __future__ import annotations

import pandas as pd

from settings import S

RETIRED = ("fact_kpi_forecast.csv", "fact_timeseries.csv")


def _log(m: str) -> None:
    print(f"[forecast_page] {m}", flush=True)


def build_dim_metric() -> pd.DataFrame:
    """
    DIRECTION: +1 where higher is good, -1 where higher is bad. Drives colour.
    DELTA_TYPE: loss_ratio moves in percentage POINTS, not percent change.
                68% -> 71% is +3 pts, not +4.4%.
    """
    return pd.DataFrame([
        {"METRIC": "claims_cost", "DISPLAY_NAME": "Expected Claims",
         "FORMAT_TYPE": "currency", "DIRECTION": -1, "DELTA_TYPE": "pct", "SORT_ORDER": 1},
        {"METRIC": "frequency", "DISPLAY_NAME": "Expected Frequency",
         "FORMAT_TYPE": "rate", "DIRECTION": -1, "DELTA_TYPE": "pct", "SORT_ORDER": 2},
        {"METRIC": "severity", "DISPLAY_NAME": "Expected Severity",
         "FORMAT_TYPE": "currency", "DIRECTION": -1, "DELTA_TYPE": "pct", "SORT_ORDER": 3},
        {"METRIC": "premium", "DISPLAY_NAME": "Expected Premium",
         "FORMAT_TYPE": "currency", "DIRECTION": 1, "DELTA_TYPE": "pct", "SORT_ORDER": 4},
        {"METRIC": "loss_ratio", "DISPLAY_NAME": "Expected Loss Ratio",
         "FORMAT_TYPE": "percent", "DIRECTION": -1, "DELTA_TYPE": "pts", "SORT_ORDER": 5},
        {"METRIC": "uw_margin", "DISPLAY_NAME": "Expected U/W Margin",
         "FORMAT_TYPE": "currency", "DIRECTION": 1, "DELTA_TYPE": "pct", "SORT_ORDER": 6},
    ])


def main() -> None:
    dim = build_dim_metric()
    dim.to_csv(S.facts_dir / "dim_metric.csv", index=False, encoding="utf-8")
    _log(f"wrote dim_metric.csv ({len(dim)} rows)")
    for name in RETIRED:
        old = S.facts_dir / name
        if old.exists():
            old.unlink()
            _log(f"removed retired {name} (Forecast figures come from step 07 only)")


if __name__ == "__main__":
    main()
