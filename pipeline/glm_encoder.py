"""
pipeline/glm_encoder.py
=======================
The one encoder for the pricing GLMs (plan H10): steps 02 (refit), 03 (pure premium) and 04 (backtest) all
use it, so a model is always scored on the columns it was trained on.

  Levels     every value is read as text, in fitting and in scoring alike ("1" and 1 are the same level);
             missing -> "Unknown"
  Rare       a level with fewer than RARE_THRESHOLD rows in the training data -> "Other"
  Columns    one-hot, first level dropped; a column identical to an earlier one is dropped (factors that are
             all "Unknown" on the same policies give identical columns and a GLM cannot separate them: its
             coefficients run to +-1e11)
  Scoring    the saved levels and columns are applied as they are: an unseen level is "Other", a missing
             training column is 0, an extra one is dropped

encoder_meta = {"category_maps": {factor: {"kept": [levels], "threshold": n}}, "columns": [...]}, stored with each
model (02_glm_refit.save_model). Models saved before this module still load: their levels are read as text too.
"""
from __future__ import annotations

import pandas as pd

RARE_THRESHOLD = 30     # method constant


def _text(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """(values as text, missing mask)."""
    missing = series.isna()
    return series.astype(object).where(~missing, "").map(str), missing


def _levels(series: pd.Series, kept: set) -> pd.Series:
    text, missing = _text(series)
    return text.where(text.isin(kept), "Other").mask(missing, "Unknown")


def _drop_duplicate_columns(X: pd.DataFrame) -> pd.DataFrame:
    seen, keep = set(), []
    for c in X.columns:
        key = (int(X[c].sum()), int(pd.util.hash_array(X[c].to_numpy()).sum()))
        if key not in seen:
            seen.add(key)
            keep.append(c)
    return X[keep]


def fit(df: pd.DataFrame, feature_cols: list[str], threshold: int = RARE_THRESHOLD) -> tuple[pd.DataFrame, dict]:
    """Learn the levels and columns from the training rows; returns (design matrix, encoder_meta)."""
    meta = {"category_maps": {}, "columns": []}
    coded = {}
    for col in feature_cols:
        text, missing = _text(df[col])
        counts = text.mask(missing, "Unknown").value_counts()
        kept = counts[counts >= threshold].index.tolist()
        meta["category_maps"][col] = {"kept": kept, "threshold": threshold}
        coded[col] = _levels(df[col], set(kept))
    X = _drop_duplicate_columns(pd.get_dummies(pd.DataFrame(coded, index=df.index)[feature_cols],
                                               drop_first=True, dtype=int))
    meta["columns"] = X.columns.tolist()
    return X, meta


def transform(df: pd.DataFrame, meta: dict) -> pd.DataFrame:
    """Apply saved levels and columns to new rows: the design matrix in the training column order."""
    cols = list(meta["category_maps"].keys())
    coded = {c: _levels(df[c], {str(k) for k in meta["category_maps"][c]["kept"]}) for c in cols}
    X = pd.get_dummies(pd.DataFrame(coded, index=df.index)[cols], drop_first=True, dtype=int)
    return X.reindex(columns=meta["columns"], fill_value=0)
