"""Determinism: the same inputs give byte-identical outputs, wherever and whenever they run.

Two teams that set up the same profile and frame the same task must get the same
instructions, consultation and decision brief. Each run uses a fresh project in a
different folder and a different PYTHONHASHSEED, so set or dict ordering that
depends on the hash seed, or a path that leaks into an output, fails here. The
consultation ID is random by design and is replaced before comparing.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.e2e.test_cli_flow import ENV, EXAMPLE, TASK_ID

SEEDS = ("0", "1", "4242")


class Run:
    """One fresh project, its outputs, under one hash seed."""

    def __init__(self, folder: Path, seed: str):
        self.project = folder
        self.env = {**ENV, "PYTHONHASHSEED": seed}
        self.outputs = {}

    def cli(self, *args) -> str:
        result = subprocess.run([sys.executable, "-m", "openguildkit", "--project", str(self.project), *map(str, args)],
                                capture_output=True, env=self.env, timeout=60)
        if result.returncode:
            raise AssertionError(f"openguildkit {' '.join(map(str, args))}: {result.stderr.decode('utf-8', 'replace')}")
        return result.stdout.decode("utf-8")

    def record(self, name: str, *args) -> None:
        self.outputs[name] = self.cli(*args)

    def play(self) -> dict:
        self.record("catalog --json", "catalog", "--json")
        self.record("recommend --json", "recommend", "--answers", EXAMPLE / "profile.json", "--json")
        self.record("preview --json", "preview", "--answers", EXAMPLE / "profile.json", "--json")
        self.cli("init", "--answers", EXAMPLE / "profile.json")
        self.outputs["AGENTS.md"] = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        self.outputs["CLAUDE.md"] = (self.project / "CLAUDE.md").read_text(encoding="utf-8")
        self.outputs["project.json"] = (self.project / ".openguildkit" / "project.json").read_text(encoding="utf-8")
        self.outputs["catalog.lock.json"] = (self.project / ".openguildkit" / "catalog.lock.json").read_text(encoding="utf-8")
        self.cli("task", "new", "--file", EXAMPLE / "brief.md", "--id", TASK_ID)
        self.record("task route --json", "task", "route", TASK_ID, "--answers", EXAMPLE / "answers.json", "--json")
        self.record("task consult", "task", "consult", TASK_ID, "--answers", EXAMPLE / "answers.json")
        consultation_id = json.loads(self.cli("task", "template", TASK_ID))["consultation_id"]
        self.record("task template", "task", "template", TASK_ID)
        self.record("task pass security", "task", "pass", TASK_ID, "security")
        review = {"consultation_id": consultation_id, **json.loads((EXAMPLE / "review.json").read_text(encoding="utf-8"))}
        review_file = self.project / "review.json"
        review_file.write_text(json.dumps(review), encoding="utf-8")
        self.cli("task", "review", TASK_ID, "--file", review_file)
        self.record("task challenge", "task", "challenge", TASK_ID)
        self.record("task deliberate", "task", "deliberate", TASK_ID)
        return {name: text.replace(consultation_id, "CONSULTATION-ID") for name, text in self.outputs.items()}


class DeterminismTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="ogk-determinism-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def test_two_teams_get_byte_identical_outputs(self):
        folders = [self.base / "team-a" / "project", self.base / "Équipe B" / "deeper" / "other-name"]
        runs = []
        for folder, seed in zip(folders, SEEDS):
            folder.mkdir(parents=True)
            runs.append(Run(folder, seed).play())
        first, second = runs
        self.assertEqual(sorted(first), sorted(second))
        for name in first:
            with self.subTest(output=name):
                self.assertEqual(first[name], second[name])
        self.assertIn("<!-- BEGIN OPENGUILDKIT", first["AGENTS.md"])
        self.assertIn("# Decision brief", first["task challenge"])
        for name, text in first.items():
            self.assertNotIn(str(self.base), text, name)

    def test_read_only_outputs_do_not_depend_on_the_hash_seed(self):
        folder = self.base / "seeds"
        folder.mkdir()
        outputs = set()
        for seed in SEEDS:
            run = Run(folder, seed)
            outputs.add((run.cli("catalog", "--json"), run.cli("brains", "--json"),
                         run.cli("recommend", "--answers", EXAMPLE / "profile.json", "--json"),
                         run.cli("references", "--json")))
        self.assertEqual(len(outputs), 1)


if __name__ == "__main__":
    unittest.main()
