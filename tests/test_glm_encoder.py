"""
The shared GLM encoder (pipeline/glm_encoder.py, plan H10): what a model is trained on is what it is scored on.
Standard library only:

    .venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import glm_encoder as E  # noqa: E402


def frame(n_a=40, n_b=35, n_rare=3, n_missing=31):
    return pd.DataFrame({"ZONE": ["A"] * n_a + ["B"] * n_b + ["R"] * n_rare + [None] * n_missing,
                         "BAND": (["low", "high"] * 60)[: n_a + n_b + n_rare + n_missing]})


class Encoder(unittest.TestCase):
    def test_rare_and_missing_levels(self):
        X, meta = E.fit(frame(), ["ZONE", "BAND"])
        kept = meta["category_maps"]["ZONE"]["kept"]
        self.assertEqual(set(kept), {"A", "B", "Unknown"})          # R (3 rows) is under the threshold
        scored = E.transform(pd.DataFrame({"ZONE": ["R", None, "new", "A"], "BAND": ["low"] * 4}), meta)
        self.assertEqual(list(scored.columns), meta["columns"])
        self.assertEqual(scored.loc[2].tolist(), scored.loc[0].tolist())   # unseen level = rare level = Other

    def test_fit_and_score_give_the_same_matrix(self):
        df = frame()
        X, meta = E.fit(df, ["ZONE", "BAND"])
        pd.testing.assert_frame_equal(X, E.transform(df, meta))

    def test_numbers_and_text_are_the_same_level(self):
        """A factor holding only numbers reads as integers on one side and text on the other: same columns."""
        df = pd.DataFrame({"CLAIMS": [0] * 40 + [1] * 40, "T": ["x"] * 80})
        X, meta = E.fit(df, ["CLAIMS", "T"])
        as_text = E.transform(pd.DataFrame({"CLAIMS": ["0"] * 40 + ["1"] * 40, "T": ["x"] * 80}), meta)
        pd.testing.assert_frame_equal(X, as_text)
        self.assertEqual(int(X.sum().sum()), 40)                       # the "1" column is set, not "Other"

    def test_models_saved_before_h10_still_score(self):
        """An old encoder_meta with integer levels scores text input as those levels."""
        meta = {"category_maps": {"CLAIMS": {"kept": [0, 1], "threshold": 30}}, "columns": ["CLAIMS_1"]}
        X = E.transform(pd.DataFrame({"CLAIMS": ["1", "0", 1, None]}), meta)
        self.assertEqual(X["CLAIMS_1"].tolist(), [1, 0, 1, 0])

    def test_identical_columns_are_dropped(self):
        """Two factors 'Unknown' on the same rows give identical dummies; only one is kept."""
        df = pd.DataFrame({"V1": [None] * 40 + ["a"] * 40, "V2": [None] * 40 + ["b"] * 40})
        X, meta = E.fit(df, ["V1", "V2"])
        self.assertEqual(X.shape[1], 1)
        self.assertFalse(np.isnan(X.to_numpy()).any())

    def test_missing_training_column_is_zero(self):
        X, meta = E.fit(frame(), ["ZONE", "BAND"])
        scored = E.transform(pd.DataFrame({"ZONE": ["A"], "BAND": ["low"]}), meta)
        self.assertEqual(list(scored.columns), meta["columns"])
        self.assertTrue((scored.to_numpy() >= 0).all())


if __name__ == "__main__":
    unittest.main()
