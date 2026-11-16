"""
pipeline/timesfm_model.py
=========================
TimesFM, Google's pretrained time-series model, shared by 04_backtest (challenger) and
07_forecast_seasonal (portfolio forecast). Optional: available() is False when the package is
not installed (pip install -r requirements-timesfm.txt), and callers fall back to seasonal naive.

Pinned (plan H11), so a rerun on the same data gives the same forecast: the package version in
requirements-timesfm.txt and the checkpoint's exact revision below. A rerun with another installed
version is logged as a warning (describe()); change PACKAGE_VERSION and REVISION together, on purpose,
after re-running the backtest.
"""

from __future__ import annotations

import numpy as np

CHECKPOINT = "google/timesfm-3.0-pytorch"
REVISION = "43046b85ec22d584a13f8098c2ed39c889e129c2"     # Hugging Face commit of the checkpoint
PACKAGE_VERSION = "3.0.2"                                  # as requirements-timesfm.txt

_model = None


def available() -> bool:
    try:
        import timesfm3  # noqa: F401
    except ImportError:
        return False
    return True


def installed_version() -> str | None:
    try:
        from importlib.metadata import version
        return version("timesfm")
    except Exception:
        return None


def describe() -> str:
    """One line for the run log: the version and checkpoint revision used, and a warning when the installed
    package is not the pinned one."""
    v = installed_version()
    text = f"TimesFM {v} ({CHECKPOINT} @ {REVISION[:12]})"
    if v != PACKAGE_VERSION:
        text += f"; WARNING: pinned version is {PACKAGE_VERSION}, results may differ from earlier runs"
    return text


def predict(contexts: list[np.ndarray], horizon: int) -> list[np.ndarray]:
    """Point forecasts for the next `horizon` steps of each history in `contexts`."""
    global _model
    if _model is None:
        from timesfm3 import ModelConfig, TimesFM3Evaluator
        _model = TimesFM3Evaluator(ModelConfig(checkpoint_path=CHECKPOINT, revision=REVISION))
    outputs = _model.predict_batch([np.asarray(c, dtype=np.float32) for c in contexts],
                                   horizon=horizon, return_quantiles=False)
    return [np.asarray(o.forecast, dtype=float) for o in outputs]
