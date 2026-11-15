"""
Step 23 (extract validation) on small made-up extracts: a clean one passes; each kind of broken extract stops the
run with the right check. Standard library only:

    .venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import settings  # noqa: E402

validate = importlib.import_module("23_validate_extract")

POLICIES = pd.DataFrame({
    "POLICY_NO": ["a", "b", "c"], "POLICY_YEAR": [2023, 2023, 2024], "CLASS_DESC": "X", "POLICY_TYPE_DESC": "T",
    "POLICY_EFFECTIVE_DATE": pd.to_datetime(["2023-01-01"] * 3),
    "POLICY_EXPIRY_DATE": pd.to_datetime(["2024-01-01"] * 3),
    "TOTAL_PREMIUM": [1.0, 2.0, 3.0], "COMMISSION": [0.1, 0.1, 0.1]})
CLAIMS = pd.DataFrame({
    "CLM_NO": ["1", "2"], "POLICY_NO": ["a", "b"], "POLICY_YEAR": [2023, 2023],
    "ACC_DATE": pd.to_datetime(["2023-05-01", "2024-12-31"]), "TOTAL_PAID": [5.0, 6.0]})


def run(files: dict) -> tuple[int, pd.DataFrame]:
    """Run step 23 on these extracts in a scratch folder; returns (exit code, its check table)."""
    d = Path(tempfile.mkdtemp())
    for name, df in files.items():
        df.to_parquet(d / f"{name}.parquet", index=False)
    validate.S = settings.S.__class__(**{**settings.S.__dict__, "raw_dir": d, "facts_dir": d / "facts",
                                         "reports_dir": d / "reports"})
    validate.results.clear()
    code = validate.main()
    return code, pd.read_csv(d / "facts" / "fact_extract_validation.csv")


class ValidateExtract(unittest.TestCase):
    def assertStops(self, files: dict, check_starts: str) -> None:
        code, table = run(files)
        self.assertEqual(code, 1)
        stops = table.loc[table["STATUS"] == "STOP", "CHECK"]
        self.assertTrue(stops.str.startswith(check_starts).any(), f"no STOP on '{check_starts}': {list(stops)}")

    def test_clean_extract_passes(self):
        code, table = run({"policies": POLICIES, "claims": CLAIMS})
        self.assertEqual(code, 0)
        self.assertFalse((table["STATUS"] == "STOP").any())

    def test_missing_needed_file_stops(self):
        self.assertStops({"policies": POLICIES}, "file present")

    def test_missing_required_column_stops(self):
        self.assertStops({"policies": POLICIES.drop(columns="COMMISSION"), "claims": CLAIMS}, "required columns")

    def test_duplicate_keys_stop(self):
        self.assertStops({"policies": pd.concat([POLICIES, POLICIES]), "claims": CLAIMS}, "key unique")

    def test_unreadable_dates_stop(self):
        self.assertStops({"policies": POLICIES.assign(POLICY_EFFECTIVE_DATE=pd.NaT), "claims": CLAIMS},
                         "POLICY_EFFECTIVE_DATE")

    def test_claims_not_finding_policies_stop(self):
        self.assertStops({"policies": POLICIES, "claims": CLAIMS.assign(POLICY_NO=["z", "y"])}, "claims find")

    def test_optional_file_missing_only_warns(self):
        code, table = run({"policies": POLICIES, "claims": CLAIMS})
        veh = table[(table["FILE"] == "vehicles") & (table["CHECK"] == "file present")]
        self.assertEqual(code, 0)
        self.assertEqual(veh["STATUS"].iloc[0], "WARN")


if __name__ == "__main__":
    unittest.main()
