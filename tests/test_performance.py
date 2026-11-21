"""Performance: time budgets that catch a slowdown of an order of magnitude.

The measures come from scripts/bench.py: real processes, median of three runs.
Budgets are ratios to the time of openguildkit --version measured in the same
run, so a slow CI runner slows the budget down with the commands. Each budget is
at most ten times the ratio docs/testing.md records, so a tenfold regression
fails, and at least three times the highest ratio seen, so a busy machine passes. The start itself is held
to a ratio of a bare interpreter start, and every command to an absolute ceiling
against hangs.
"""

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench.py"
spec = importlib.util.spec_from_file_location("openguildkit_bench", SCRIPT)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

# Times openguildkit --version: at most ten times the lowest ratio docs/testing.md records, and at least
# three times the highest.
BUDGETS = {
    "catalog --json": 15,
    "init": 12,
    "task consult": 12,
    f"task list ({bench.TASKS} tasks)": 20,
    "references search": 12,
    f"guard --hook pre-edit ({bench.TASKS} tasks)": 20,
    f"guard --hook prompt ({bench.TASKS} tasks)": 20,
    f"detect (monorepo, {bench.PACKAGES} packages)": 14,
    f"init (monorepo, {bench.PACKAGES} packages)": 16,
}
START_BUDGET = 25  # times a bare interpreter start
CEILING_SECONDS = 60


class PerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = bench.measure(repeat=3)
        if cls.over_budget(cls.results):
            # Measure once more before failing: a burst of load rarely lasts two measures, a regression does.
            again = bench.measure(repeat=3)
            cls.results = {name: min(item, again[name], key=lambda run: run["x_start"])
                           for name, item in cls.results.items()}

    @staticmethod
    def over_budget(results):
        start = results["start (--version)"]["median"] / results["python -c pass"]["median"]
        return start >= START_BUDGET or any(results[name]["x_start"] >= budget for name, budget in BUDGETS.items())

    def test_every_measured_command_has_a_budget(self):
        self.assertEqual(set(self.results) - {"python -c pass", "start (--version)"}, set(BUDGETS))

    def test_the_cli_starts_quickly(self):
        start = self.results["start (--version)"]["median"]
        floor = self.results["python -c pass"]["median"]
        self.assertLess(start / floor, START_BUDGET, f"--version takes {start:.3f} s, a bare start {floor:.3f} s")

    def test_each_command_stays_within_its_budget(self):
        for name, budget in BUDGETS.items():
            with self.subTest(command=name):
                item = self.results[name]
                self.assertLess(item["median"], CEILING_SECONDS)
                self.assertLess(item["x_start"], budget, f"{name}: {item['median']:.3f} s, "
                                f"{item['x_start']:.1f} times the start; budget {budget}")


if __name__ == "__main__":
    unittest.main()
