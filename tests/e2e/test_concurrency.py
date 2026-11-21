"""Concurrency: real processes write and read one project at the same time.

Every outcome asserted here holds whatever the scheduling: the lock makes writers
wait their turn, and atomic replacement means a reader sees a whole file or none.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from openguildkit.cli import main
from tests.e2e.test_cli_flow import ENV, EXAMPLE, EXPECTED_BOARDS, TASK_ID, CliTestCase

HOLD_LOCK = (
    "import sys\n"
    "from pathlib import Path\n"
    "from openguildkit.storage import project_lock\n"
    "with project_lock(Path(sys.argv[1])):\n"
    "    print('held', flush=True)\n"
    "    sys.stdin.readline()\n"
)
LOCK_MESSAGE = "Another OpenGuildKit command is writing this project. Try again."


class ConcurrencyTestCase(CliTestCase):
    def start(self, *args, env=None):
        return subprocess.Popen(
            self.command(*args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            env=env or ENV,
        )

    def run_together(self, commands, env=None):
        """Start every command before waiting for any, and return (code, stdout, stderr) in order."""
        processes = [self.start(*command, env=env) for command in commands]
        outputs = [process.communicate(timeout=120) for process in processes]
        return [(process.returncode, *output) for process, output in zip(processes, outputs)]

    def hold_lock(self):
        """A separate process that holds the project lock until the returned function is called."""
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLD_LOCK, str(self.project / ".openguildkit")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", env=ENV,
        )
        self.assertEqual(holder.stdout.readline().strip(), "held")

        def release():
            if holder.poll() is None:
                holder.stdin.write("\n")
                holder.stdin.flush()
                holder.communicate(timeout=30)

        self.addCleanup(release)
        return release

    def task_files(self):
        return sorted((self.project / ".openguildkit" / "tasks").glob("*.json"))


class LockTests(ConcurrencyTestCase):
    def test_six_board_passes_started_together_all_succeed(self):
        self.consult_example()
        boards = sorted(EXPECTED_BOARDS)
        outcomes = self.run_together([("task", "pass", TASK_ID, board, "--mode", "subagent") for board in boards])
        for board, (code, stdout, stderr) in zip(boards, outcomes):
            self.assertEqual(code, 0, f"{board}: {stderr}")
            self.assertIn(f"# Board pass: {board}", stdout)
        progress = json.loads(self.run_cli("task", "progress", TASK_ID, "--json").stdout)
        self.assertEqual({item["board"]: item["status"] for item in progress}, dict.fromkeys(boards, "started"))

    def test_a_writer_waits_for_the_lock_and_then_succeeds(self):
        self.init_example()
        release = self.hold_lock()
        writer = self.start("task", "new", "Waits for the lock.", "--id", "waiting")
        time.sleep(1.0)
        self.assertIsNone(writer.poll(), "the writer gave up instead of waiting")
        release()
        stdout, stderr = writer.communicate(timeout=60)
        self.assertEqual(writer.returncode, 0, stderr)
        self.assertEqual(stdout.strip(), "waiting")

    def test_a_writer_gives_up_after_the_configured_wait_with_the_usual_message(self):
        self.init_example()
        self.hold_lock()
        started = time.monotonic()
        code, _, stderr = self.run_together(
            [("task", "new", "Cannot write.", "--id", "blocked")], env={**ENV, "OPENGUILDKIT_LOCK_TIMEOUT": "0.5"}
        )[0]
        elapsed = time.monotonic() - started
        self.assertEqual(code, 1)
        self.assertEqual(stderr.strip(), f"OpenGuildKit: {LOCK_MESSAGE}")
        self.assertGreaterEqual(elapsed, 0.5)
        self.assertLess(elapsed, 30)
        self.assertEqual(self.task_files(), [])

    def test_an_invalid_wait_setting_is_a_clean_error(self):
        self.init_example()
        result = subprocess.run(
            self.command("task", "new", "x", "--id", "x"), capture_output=True, text=True, encoding="utf-8",
            env={**ENV, "OPENGUILDKIT_LOCK_TIMEOUT": "soon"}, timeout=60,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("OPENGUILDKIT_LOCK_TIMEOUT", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_read_commands_never_wait_for_the_lock(self):
        self.consult_example()
        self.hold_lock()
        quick = {**ENV, "OPENGUILDKIT_LOCK_TIMEOUT": "0"}
        reads = [
            ("task", "list", "--json"), ("task", "show", TASK_ID), ("task", "progress", TASK_ID),
            ("task", "brief", TASK_ID), ("task", "template", TASK_ID), ("task", "blockers", TASK_ID),
            ("check",), ("catalog", "--json"), ("guard", "--json"),
        ]
        for command, (code, _, stderr) in zip(reads, self.run_together(reads, env=quick)):
            self.assertEqual(code, 0, f"{' '.join(command)}: {stderr}")
        hook = subprocess.run(self.command("guard", "--hook", "prompt"), input="{}", capture_output=True, text=True,
                              encoding="utf-8", env=quick, timeout=60)
        self.assertEqual(hook.returncode, 0, hook.stderr)


class ParallelWriteTests(ConcurrencyTestCase):
    def test_tasks_created_together_all_exist_as_valid_json(self):
        self.init_example()
        ids = [f"parallel-{index}" for index in range(8)]
        outcomes = self.run_together([("task", "new", f"Parallel brief {name}.", "--id", name) for name in ids])
        for name, (code, stdout, stderr) in zip(ids, outcomes):
            self.assertEqual(code, 0, f"{name}: {stderr}")
            self.assertEqual(stdout.strip(), name)
        records = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in self.task_files()}
        self.assertEqual(sorted(records), sorted(ids))
        for name, record in records.items():
            self.assertEqual(record["brief"], f"Parallel brief {name}.")
        self.assertEqual([path.name for path in (self.project / ".openguildkit" / "tasks").iterdir()
                          if path.name.endswith(".tmp")], [])

    def test_the_same_task_id_created_twice_is_refused_once_without_overwrite(self):
        self.init_example()
        briefs = ["First version of the brief.", "Second version of the brief."]
        outcomes = self.run_together([("task", "new", brief, "--id", "twin") for brief in briefs])
        codes = sorted(code for code, _, _ in outcomes)
        self.assertEqual(codes, [0, 1])
        winner = next(brief for brief, (code, _, _) in zip(briefs, outcomes) if code == 0)
        loser_error = next(stderr for code, _, stderr in outcomes if code == 1)
        self.assertEqual(loser_error.strip(), "OpenGuildKit: Task already exists: twin")
        self.assertEqual(self.task_record_of("twin")["brief"], winner)

    def test_two_submissions_for_one_board_leave_one_whole_entry(self):
        self.consult_example()
        entry = next(item for item in json.loads((EXAMPLE / "review.json").read_text(encoding="utf-8"))["boards"]
                     if item["id"] == "security")
        self.run_cli("task", "pass", TASK_ID, "security", "--mode", "subagent")
        variants = [{**entry, "recommendation": f"{entry['recommendation']} (variant {index})"} for index in (1, 2)]
        files = [self.write_json(f"security-{index}.json", variant) for index, variant in enumerate(variants)]
        outcomes = self.run_together([("task", "submit", TASK_ID, "--file", path) for path in files])
        for code, stdout, stderr in outcomes:
            self.assertEqual((code, stdout.strip()), (0, "done"), stderr)
        recorded = self.task_record()["passes"]["boards"]["security"]
        self.assertEqual(recorded["status"], "done")
        self.assertIn(recorded["entry"]["recommendation"], [variant["recommendation"] for variant in variants])

    def test_passes_and_submissions_for_different_boards_lose_nothing(self):
        self.consult_example()
        entries = {item["id"]: item for item in json.loads((EXAMPLE / "review.json").read_text(encoding="utf-8"))["boards"]}
        self.run_together([("task", "pass", TASK_ID, board) for board in entries])
        files = [self.write_json(f"{board}.json", entry) for board, entry in entries.items()]
        for code, _, stderr in self.run_together([("task", "submit", TASK_ID, "--file", path) for path in files]):
            self.assertEqual(code, 0, stderr)
        progress = json.loads(self.run_cli("task", "progress", TASK_ID, "--json").stdout)
        self.assertTrue(all(item["status"] == "done" for item in progress), progress)
        self.assertEqual(self.run_cli("task", "review", TASK_ID, "--summary", "Parallel passes.").stdout.strip(), "reviewed")

    def test_readers_running_during_writes_always_see_whole_records(self):
        self.consult_example()
        failures, runs = [], []
        stop = threading.Event()

        def read_loop(*command):
            while not stop.is_set():
                result = subprocess.run(self.command(*command), capture_output=True, text=True, encoding="utf-8",
                                        env=ENV, timeout=60)
                runs.append(command[1])
                try:
                    if result.returncode != 0:
                        raise ValueError(result.stderr)
                    json.loads(result.stdout)
                except ValueError as error:
                    failures.append(f"{' '.join(command)}: {error}")

        readers = [threading.Thread(target=read_loop, args=command)
                   for command in (("task", "list", "--json"), ("task", "show", TASK_ID))]
        for reader in readers:
            reader.start()
        try:
            for round_number in range(3):
                writes = [("task", "pass", TASK_ID, board) for board in sorted(EXPECTED_BOARDS)]
                writes += [("task", "new", f"Brief {round_number}-{index}.", "--id", f"r{round_number}-{index}")
                           for index in range(3)]
                for code, _, stderr in self.run_together(writes):
                    self.assertEqual(code, 0, stderr)
        finally:
            stop.set()
            for reader in readers:
                reader.join(timeout=120)
        self.assertEqual(failures, [])
        self.assertIn("list", runs)
        self.assertIn("show", runs)

    def task_record_of(self, task_id):
        return json.loads(self.run_cli("task", "show", task_id).stdout)


def _in_process(project, *arguments, typed=None):
    """Run the CLI in this process; a typed answer stands for the person at the terminal."""
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr), \
            patch("sys.stdin.isatty", return_value=typed is not None), patch("builtins.input", return_value=typed or ""):
        code = main(["--project", str(project), *map(str, arguments)])
    if code != 0:
        raise AssertionError(f"openguildkit {' '.join(map(str, arguments))} -> {code}: {stderr.getvalue()}")
    return stdout.getvalue()


@unittest.skipIf(shutil.which("git") is None, "needs git")
class TwoClonesTests(unittest.TestCase):
    """Two people work on two clones, each approves and exports a task, then the branches merge."""

    def setUp(self):
        import tempfile

        self.base = Path(tempfile.mkdtemp(prefix="ogk-clones-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def git(self, folder, *arguments):
        environment = {**ENV, "GIT_AUTHOR_NAME": "Test Person", "GIT_AUTHOR_EMAIL": "person@example.test",
                       "GIT_COMMITTER_NAME": "Test Person", "GIT_COMMITTER_EMAIL": "person@example.test"}
        result = subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *arguments],
                                cwd=folder, capture_output=True, text=True, encoding="utf-8", env=environment, timeout=60)
        self.assertEqual(result.returncode, 0, f"git {' '.join(arguments)}: {result.stderr}")
        return result.stdout

    def approved_and_exported(self, clone, task_id, brief):
        _in_process(clone, "task", "new", brief, "--id", task_id)
        _in_process(clone, "task", "consult", task_id, "--answers", EXAMPLE / "answers.json")
        template = json.loads(_in_process(clone, "task", "template", task_id))
        review = {"consultation_id": template["consultation_id"],
                  **json.loads((EXAMPLE / "review.json").read_text(encoding="utf-8"))}
        (clone / "review.json").write_text(json.dumps(review), encoding="utf-8")
        _in_process(clone, "task", "review", task_id, "--file", clone / "review.json")
        (clone / "review.json").unlink()
        _in_process(clone, "task", "decide", task_id, "--approve", "--by", "Test Owner", "--reason", "Agreed",
                    typed=task_id)
        _in_process(clone, "task", "export", task_id)

    def test_each_clone_runs_a_task_and_the_branches_merge_without_conflict(self):
        origin = self.base / "origin"
        origin.mkdir()
        self.git(origin, "init", "-q")
        _in_process(origin, "init", "--answers", EXAMPLE / "profile.json")
        self.git(origin, "add", "-A")
        self.git(origin, "commit", "-q", "-m", "Set up OpenGuildKit")
        clones = {name: self.base / name for name in ("alice", "bruno")}
        for name, clone in clones.items():
            self.git(self.base, "clone", "-q", str(origin), name)
            self.git(clone, "checkout", "-q", "-b", name)
            self.approved_and_exported(clone, f"{name}-task", EXAMPLE.joinpath("brief.md").read_text(encoding="utf-8"))
            self.assertEqual(self.git(clone, "status", "--porcelain", "--", ".openguildkit/tasks"), "")
            self.git(clone, "add", "-A")
            self.git(clone, "commit", "-q", "-m", f"Decision of {name}")
        alice, bruno = clones["alice"], clones["bruno"]
        self.git(alice, "pull", "-q", "--no-rebase", "--no-edit", str(bruno), "bruno")
        decisions = sorted(path.name for path in (alice / ".openguildkit" / "decisions").iterdir())
        self.assertEqual(decisions, ["alice-task.json", "bruno-task.json"])
        self.assertEqual(self.git(alice, "diff", "--name-only", "--diff-filter=U"), "")
        _in_process(alice, "check")
        for path in (alice / ".openguildkit" / "decisions").iterdir():
            json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
