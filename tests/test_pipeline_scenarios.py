"""
The whole pipeline on made-up companies (tests/make_company.py, tests/run_scenario.py): each must finish with every
reconcile check passing, or stop at the step the scenario names (e.g. validation, for an extract without claims).
About 3 minutes a scenario, so they run only when asked:

    set AMAN_SLOW_TESTS=1   (PowerShell: $env:AMAN_SLOW_TESTS=1)
    .venv/Scripts/python.exe -m unittest discover -s tests -v

Without AMAN_SLOW_TESTS the fast tests run and these are skipped. One scenario on its own:
    .venv/Scripts/python.exe tests/run_scenario.py missing_year
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_scenario  # noqa: E402

SLOW = os.environ.get("AMAN_SLOW_TESTS") == "1"


@unittest.skipUnless(SLOW, "whole-pipeline scenarios: set AMAN_SLOW_TESTS=1")
class PipelineScenarios(unittest.TestCase):
    def check(self, name: str) -> None:
        outcome, work, tail = run_scenario.run(name)
        self.assertEqual(outcome, run_scenario.SCENARIOS[name][1], f"{name} in {work}:\n{tail}")


for _name in run_scenario.SCENARIOS:
    setattr(PipelineScenarios, f"test_{_name}", lambda self, n=_name: self.check(n))


if __name__ == "__main__":
    unittest.main()
