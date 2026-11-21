"""Upgrade: a project written by an older release still works with this one.

Each fixture in tests/fixtures/upgrade holds the exact files a released version
wrote (its own code, run from the release tag): profile, instructions, ignore
file, project lock and task records, one of them approved. Bundling them in one
JSON file keeps the task records out of reach of the fixture's own .gitignore.
"""

import json
from pathlib import Path

from tests.e2e.test_cli_flow import CliTestCase

FIXTURES = Path(__file__).with_name("fixtures") / "upgrade"


class UpgradeTestCase(CliTestCase):
    release = ""

    def setUp(self):
        super().setUp()
        self.bundle = json.loads((FIXTURES / f"{self.release}.json").read_text(encoding="utf-8"))
        for name, content in self.bundle["files"].items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
        self.approved = self.bundle["approved_task"]

    def assert_upgrades(self):
        """check names the next step, update and update --apply succeed, and the project is current after."""
        before = self.run_cli("check", expect=None)
        if before.returncode:
            self.assertEqual(before.stderr.strip(), "OpenGuildKit: OpenGuildKit instructions are out of date. Run openguildkit sync.")
        self.assertIn("no catalog lock", self.run_cli("update").stdout)
        self.assertIn("now pins the installed catalog", self.run_cli("update", "--apply").stdout)
        self.assertTrue((self.project / ".openguildkit" / "catalog.lock.json").is_file())
        self.assertEqual(self.run_cli("check").stdout.strip(), "OpenGuildKit instructions match the project profile.")
        self.assertIn("match the installed catalog", self.run_cli("update").stdout)
        self.run_cli("sync")
        self.run_cli("check")

    def assert_tasks_still_load(self):
        rows = {row["id"]: row for row in json.loads(self.run_cli("task", "list", "--json").stdout)}
        self.assertEqual(rows[self.approved]["status"], "approved")
        self.assertEqual(rows["open-draft"]["status"], "draft")
        self.assertEqual(self.run_cli("task", "show", self.approved).stdout.count('"status": "approved"'), 1)
        handoff = self.run_cli("task", "handoff", self.approved, expect=1)
        self.assertEqual(
            handoff.stderr.strip(),
            "OpenGuildKit: This approval has no seal. It was edited outside the CLI or predates sealed decisions: open a new task.",
        )
        self.assertEqual(json.loads(self.run_cli("guard", "--json").stdout)["covered_by"], [])

    def assert_old_draft_continues(self):
        answers = Path(__file__).resolve().parents[1] / "examples" / "request-status" / "answers.json"
        self.run_cli("task", "consult", "open-draft", "--answers", answers)
        progress = json.loads(self.run_cli("task", "progress", "open-draft", "--json").stdout)
        self.assertTrue(progress)
        self.run_cli("task", "pass", "open-draft", progress[0]["board"])


class PreviousReleaseTests(UpgradeTestCase):
    """v0.16.1, the release before the catalog lock existed."""

    release = "v0.16.1"

    def test_the_previous_release_project_passes_check_update_and_update_apply(self):
        self.assert_upgrades()

    def test_its_tasks_load_and_an_old_approval_asks_for_a_new_task(self):
        self.assert_tasks_still_load()

    def test_its_draft_continues_through_consultation(self):
        self.run_cli("sync")
        self.assert_old_draft_continues()


class FirstReleaseTests(UpgradeTestCase):
    """v0.1.0, the oldest release; its insurance sector has since left the catalog."""

    release = "v0.1.0"

    def test_a_removed_sector_is_a_clean_error_naming_the_sector(self):
        for command in (("check",), ("update",), ("task", "list")):
            result = self.run_cli(*command, expect=1)
            self.assertEqual(result.stderr.strip(), "OpenGuildKit: Unknown sector: insurance. Run openguildkit catalog.")
            self.assertNotIn("Traceback", result.stderr)

    def test_once_the_sector_is_replaced_the_project_upgrades_and_its_tasks_load(self):
        profile = self.project / ".openguildkit" / "project.json"
        sectors = json.loads(self.run_cli("catalog", "--json").stdout)["sectors"]
        self.assertNotIn("insurance", sectors)
        profile.write_text(profile.read_text(encoding="utf-8").replace('"insurance"', '"general"'), encoding="utf-8")
        self.assert_upgrades()
        self.assert_tasks_still_load()
        self.assert_old_draft_continues()


if __name__ == "__main__":
    import unittest

    unittest.main()
