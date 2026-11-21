"""Every CLI command and task action is run by at least one test.

The test walks the argparse tree and looks in the test files for an invocation:
the group and action as consecutive arguments, such as "task", "pass", or a
top-level command as an argument of its own. A new command without a test fails here.
"""

import argparse
import re
import unittest
from pathlib import Path

from openguildkit.cli import _parser

TESTS = Path(__file__).resolve().parent


def _subcommands(parser: argparse.ArgumentParser) -> dict:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def command_paths() -> list[tuple[str, ...]]:
    """Each runnable command: a top-level command, or a group and its action."""
    paths = []
    for name, command in _subcommands(_parser()).items():
        actions = _subcommands(command)
        paths.extend([(name, action) for action in actions] if actions else [(name,)])
    return paths


def _pattern(path: tuple[str, ...]) -> re.Pattern:
    quoted = [f"[\"']{re.escape(part)}[\"']" for part in path]
    return re.compile(r"[(\[,]\s*" + r",\s*".join(quoted) + r"\s*[,)\]]")


def _test_sources() -> str:
    own = Path(__file__).resolve()
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(TESTS.rglob("test_*.py")) if path != own)


class CliCoverageTests(unittest.TestCase):
    def test_the_walk_finds_the_known_commands(self):
        paths = command_paths()
        self.assertIn(("task", "pass"), paths)
        self.assertIn(("storybook", "compose"), paths)
        self.assertIn(("deliverable", "list"), paths)
        self.assertIn(("check",), paths)
        self.assertGreater(len(paths), 50)

    def test_every_command_and_task_action_is_invoked_by_a_test(self):
        sources = _test_sources()
        missing = [" ".join(path) for path in command_paths() if not _pattern(path).search(sources)]
        self.assertEqual(missing, [], "Add a test that runs each of these commands through the CLI.")

    def test_the_search_does_not_match_a_bare_mention(self):
        self.assertIsNone(_pattern(("task", "waive")).search('help = "task waive needs a terminal"'))
        self.assertIsNotNone(_pattern(("task", "waive")).search('run_cli("task", "waive", TASK_ID)'))
        self.assertIsNotNone(_pattern(("sync",)).search('main(["--project", str(root), "sync"])'))


if __name__ == "__main__":
    unittest.main()
