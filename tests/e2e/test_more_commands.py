"""Commands that only had unit tests of their functions, run here through the CLI.

task deliberate, respond, blockers, clear and waive, storybook compose and
deliverable list: each test checks the output and the exit code.
"""

import copy
import json
import os
import subprocess
import unittest
from datetime import date, timedelta

from tests.e2e.test_cli_flow import ENV, EXAMPLE, TASK_ID, CliTestCase


class ReviewedTaskTestCase(CliTestCase):
    def review_with_blockers(self):
        """The example review with a blocking security board, recorded through the CLI."""
        review = self.example_review()
        security = next(entry for entry in review["boards"] if entry["id"] == "security")
        security["challenge"]["verdict"] = "block"
        security["challenge"]["blockers"] = [
            {"finding": "No test proves a user cannot read another user's request.", "owner": "Security lead",
             "clears_when": "An automated test with two synthetic accounts passes."},
            {"finding": "Retention of request history is not decided.", "owner": "Data protection officer",
             "clears_when": "The retention period is recorded."},
        ]
        self.run_cli("task", "review", TASK_ID, "--file", self.write_json("review.json", review))


class DeliberationCommandTests(ReviewedTaskTestCase):
    def test_deliberate_prints_the_objections_and_respond_records_the_answers(self):
        self.consult_example()
        self.run_cli("task", "deliberate", TASK_ID, expect=1)
        self.record_example_review()
        request = json.loads(self.run_cli("task", "deliberate", TASK_ID).stdout)
        self.assertIn("security-to-product", [item["id"] for item in request["objections"]])
        payload = copy.deepcopy(request["template"])
        for response in payload["responses"]:
            response.update(position="maintain", response="Our evidence still holds.", evidence_refs=[1])
        incomplete = self.run_cli("task", "respond", TASK_ID, "--file", self.write_json("d.json", payload), expect=1)
        self.assertIn("decision the responsible person", incomplete.stderr)
        self.assertTrue(incomplete.stderr.startswith("OpenGuildKit: "))
        payload["decision_needed"] = "Choose between shipping read-only now or after the access tests."
        result = self.run_cli("task", "respond", TASK_ID, "--file", self.write_json("d.json", payload))
        self.assertEqual(result.stdout.strip(), f"0 resolved, {len(request['objections'])} open")
        self.assertEqual(self.task_record()["deliberation"]["decision_needed"], payload["decision_needed"])
        self.assertIn(payload["decision_needed"], self.run_cli("task", "challenge", TASK_ID).stdout)


class BlockerCommandTests(ReviewedTaskTestCase):
    def test_blockers_lists_each_blocker_and_clear_records_its_evidence(self):
        self.consult_example()
        self.review_with_blockers()
        listing = self.run_cli("task", "blockers", TASK_ID).stdout
        self.assertIn("security:1 [open] No test proves a user cannot read another user's request.", listing)
        self.assertIn("owner: Security lead; clears when: An automated test with two synthetic accounts passes.", listing)
        items = json.loads(self.run_cli("task", "blockers", TASK_ID, "--json").stdout)
        self.assertEqual([item["id"] for item in items], ["security:1", "security:2"])

        unknown = self.run_cli("task", "clear", TASK_ID, "security:9", "--by", "Alex", "--evidence", "x", "--supports", "y",
                               expect=1)
        self.assertTrue(unknown.stderr.startswith("OpenGuildKit: "))
        missing_file = self.run_cli("task", "clear", TASK_ID, "security:1", "--by", "Alex", "--evidence",
                                    "tests/test_access.py", "--supports", "Cross-account access is refused.", expect=1)
        self.assertIn("existing file or folder of the project, or an https link", missing_file.stderr)
        (self.project / "tests").mkdir()
        (self.project / "tests" / "test_access.py").write_text("def test_access(): pass\n", encoding="utf-8")
        cleared = self.run_cli("task", "clear", TASK_ID, "security:1", "--by", "Alex", "--evidence",
                               "tests/test_access.py", "--supports", "Cross-account access is refused.")
        self.assertEqual(cleared.stdout.strip(), "cleared")
        listing = self.run_cli("task", "blockers", TASK_ID).stdout
        self.assertIn("security:1 [cleared by Alex]", listing)
        self.assertIn("security:2 [open]", listing)
        missing = self.run_cli("task", "clear", TASK_ID, "security:2", "--by", "Alex", expect=2)
        self.assertIn("--evidence", missing.stderr)

    def test_a_task_without_blockers_says_so(self):
        self.consult_example()
        self.record_example_review()
        self.assertEqual(self.run_cli("task", "blockers", TASK_ID).stdout.strip(), "No blockers.")
        self.assertEqual(json.loads(self.run_cli("task", "blockers", TASK_ID, "--json").stdout), [])


class WaiverCommandTests(ReviewedTaskTestCase):
    def setUp(self):
        super().setUp()
        profile = json.loads((EXAMPLE / "profile.json").read_text(encoding="utf-8"))
        profile["waivers"] = {"allowed_by": ["Camille Martin"], "max_days": 30}
        self.run_cli("init", "--answers", self.write_json("profile.json", profile))
        self.run_cli("task", "new", "--file", EXAMPLE / "brief.md", "--id", TASK_ID)
        self.run_cli("task", "consult", TASK_ID, "--answers", EXAMPLE / "answers.json")
        self.review_with_blockers()
        self.until = (date.today() + timedelta(days=10)).isoformat()

    def waive(self, by="Camille Martin"):
        return ("task", "waive", TASK_ID, "security:2", "--by", by, "--reason", "Internal pilot only",
                "--scope", "Internal pilot", "--until", self.until)

    def test_a_waiver_without_a_terminal_is_refused(self):
        refused = self.run_cli(*self.waive(), expect=1)
        self.assertEqual(refused.stderr.strip(), "OpenGuildKit: A waiver needs a person at an interactive terminal.")
        self.assertIn("security:2 [open]", self.run_cli("task", "blockers", TASK_ID).stdout)

    @unittest.skipUnless(os.name == "posix", "the typed confirmation needs a pseudo-terminal, available on Linux and macOS")
    def test_a_person_at_a_terminal_waives_within_the_policy(self):
        code, _, stderr = self.in_terminal(self.waive(by="Sam"), TASK_ID)
        self.assertEqual(code, 1)
        self.assertIn("cannot grant waivers", stderr)
        code, _, stderr = self.in_terminal(self.waive(), "wrong-id")
        self.assertEqual(code, 1)
        self.assertTrue(stderr.strip().endswith("OpenGuildKit: Waiver cancelled."), stderr)
        code, stdout, stderr = self.in_terminal(self.waive(), TASK_ID)
        self.assertEqual((code, stdout.strip()), (0, "waived"), stderr)
        self.assertIn("security:2 [waived by Camille Martin]", self.run_cli("task", "blockers", TASK_ID).stdout)

    def in_terminal(self, args, typed):
        master, slave = os.openpty()
        try:
            process = subprocess.Popen(self.command(*args), stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding="utf-8", env=ENV)
            os.close(slave)
            slave = None
            os.write(master, f"{typed}\n".encode())
            stdout, stderr = process.communicate(timeout=60)
        finally:
            if slave is not None:
                os.close(slave)
            os.close(master)
        return process.returncode, stdout, stderr


class StorybookComposeCommandTests(CliTestCase):
    def test_compose_adds_updates_and_removes_a_ref(self):
        self.init_example()
        missing = self.run_cli("storybook", "compose", "company", "--url", "https://design.example.test", expect=1)
        self.assertIn("No Storybook configuration in .storybook", missing.stderr)
        (self.project / ".storybook").mkdir()
        (self.project / ".storybook" / "main.ts").write_text("export default {};\n", encoding="utf-8")
        added = self.run_cli("storybook", "compose", "company", "--url", "https://design.example.test/", "--title",
                             "Example design system").stdout
        self.assertIn(".storybook/openguildkit-refs.json: company (https://design.example.test)", added)
        self.assertIn("Add these lines to .storybook/main.ts", added)
        refs = json.loads((self.project / ".storybook" / "openguildkit-refs.json").read_text(encoding="utf-8"))
        self.assertEqual(refs, {"company": {"title": "Example design system", "url": "https://design.example.test"}})
        as_json = json.loads(self.run_cli("storybook", "compose", "brand", "--url", "https://brand.example.test",
                                          "--json").stdout)
        self.assertEqual(sorted(as_json["refs"]), ["brand", "company"])
        self.assertIn("https", self.run_cli("storybook", "compose", "brand", "--url", "ftp://brand.example.test",
                                            expect=1).stderr)
        removed = self.run_cli("storybook", "compose", "brand", "--remove").stdout
        self.assertNotIn("brand", removed)
        self.run_cli("storybook", "compose", "brand", "--remove", expect=1)


class DeliverableListCommandTests(CliTestCase):
    def test_list_shows_every_template_with_its_reviewers(self):
        self.init_example()
        listing = self.run_cli("deliverable", "list").stdout.splitlines()
        ids = [line.split()[0] for line in listing]
        self.assertIn("campaign-brief", ids)
        self.assertEqual(ids, sorted(ids))
        for line in listing:
            self.assertRegex(line, r"\[[a-z-]+(, [a-z-]+)*\]$")
        shown = self.run_cli("deliverable", "show", ids[0]).stdout
        self.assertIn("Checks:", shown)


if __name__ == "__main__":
    unittest.main()
