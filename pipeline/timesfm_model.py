"""
pipeline/timesfm_model.py
=========================
TimesFM, Google's pretrained time-series model, shared by 04_backtest (challenger) and
07_forecast_seasonal (portfolio forecast). Optional: available() is False when the package is
not installed (pip install "timesfm[torch]"), and callers fall back to seasonal naive.
"""

from __future__ import annotations

import numpy as np

CHECKPOINT = "google/timesfm-3.0-pytorch"

_model = None


def available() -> bool:
    try:
        import timesfm3  # noqa: F401
    except ImportError:
        return False
    return True


def predict(contexts: list[np.ndarray], horizon: int) -> list[np.ndarray]:
    """Point forecasts for the next `horizon` steps of each history in `contexts`."""
    global _model
    if _model is None:
        from timesfm3 import ModelConfig, TimesFM3Evaluator
        _model = TimesFM3Evaluator(ModelConfig(checkpoint_path=CHECKPOINT))
    outputs = _model.predict_batch([np.asarray(c, dtype=np.float32) for c in contexts],
                                   horizon=horizon, return_quantiles=False)
    return [np.asarray(o.forecast, dtype=float) for o in outputs]
