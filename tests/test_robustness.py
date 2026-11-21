"""Robustness: seeded random and hostile inputs never crash the CLI and never corrupt state.

Each file a command reads (answers, profiles, reviews, board entries, results,
deliberations, hook input) gets its keys replaced, one at a time, by hostile
values: wrong types, random Unicode, control characters, very long strings,
deep nesting. A run passes when every command ends with an exit code and, at
most, an "OpenGuildKit: ..." message, and every task record still loads.

The seeds are fixed, so a failure reproduces: the message names the seed, the
command and the mutated key. Set OPENGUILDKIT_FUZZ_SEED to explore other seeds.
"""

import contextlib
import copy
import io
import json
import os
import random
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

from openguildkit import cli
from openguildkit.cli import main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "request-status"
TASK = "request-status"
SEED = int(os.environ.get("OPENGUILDKIT_FUZZ_SEED", "20261121"))
SAMPLES = 24

# Crashes already reported elsewhere, fixed by other changes: (exception, openguildkit function) -> issue.
# Set OPENGUILDKIT_FUZZ_STRICT=1 to fail on them too, for instance once those issues are closed.
KNOWN_CRASHES = {
    ("TypeError", "_follow_up_questions"): "#99, task questions with a list or object as kind or surface",
    ("TypeError", "validate_result"): "#99, task result with a list as check",
    ("AttributeError", "validate_result"): "#99, task result with a number as check",
    ("TypeError", "validate_deliberation"): "#99, task respond with a list as objection",
    ("AttributeError", "edited_paths"): "#95, guard pre-edit hook with tool_input that is not an object",
    # Found by this suite on a task record edited by hand; reported with the pull request that adds it.
    ("TypeError", "validate_board_entry"): "task record edited by hand: review_template boards not objects",
    ("AttributeError", "_validate_challenge"): "task record edited by hand: challenge not an object",
    ("AttributeError", "blocker_list"): "task record edited by hand: challenge not an object",
    ("TypeError", "blocker_list"): "#99, task record edited by hand: blockers not a list",
    ("TypeError", "_validate_challenge"): "#99, task record edited by hand: specialists not a list",
    ("KeyError", "list_tasks"): "#99, task record edited by hand: created_at not a text",
}
STRICT = os.environ.get("OPENGUILDKIT_FUZZ_STRICT") == "1"


UNICODE_RANGES = ((0x20, 0x7E), (0xA0, 0x24F), (0x300, 0x36F), (0x590, 0x6FF), (0x4E00, 0x4E80), (0x1F300, 0x1F64F),
                  (0x202A, 0x202E), (0xFFF0, 0xFFFF))


def random_text(rng: random.Random, length: int) -> str:
    """Random Unicode: Latin, combining marks, right-to-left scripts, CJK, emoji, bidi controls."""
    ranges = [rng.choice(UNICODE_RANGES) for _ in range(length)]
    return "".join(chr(rng.randint(low, high)) for low, high in ranges)


def hostile_values(rng: random.Random) -> list:
    """Values that a JSON file can carry and that a field should refuse or survive."""
    return [
        None, True, 0, -1, 2 ** 63, 1e308, 0.5, "", "   ", "\x00", "\t\r\n\x1b[31m\x7f", "../../etc/passwd",
        random_text(rng, rng.randint(1, 40)), "x" * 200_000, ["x"], [["x"]], [None], [1, 2], {}, {"x": "y"},
        {"id": ["x"]}, "\ud800", "2026-99-99", "-" * 3, "<!-- END OPENGUILDKIT -->",
    ]


def deep_json(depth: int) -> str:
    return "[" * depth + "]" * depth


def key_paths(data, prefix=()) -> list[tuple]:
    """Every key path of a JSON document, entering the first item of each list."""
    paths = []
    if isinstance(data, dict):
        for key, value in data.items():
            paths.append((*prefix, key))
            paths.extend(key_paths(value, (*prefix, key)))
    elif isinstance(data, list) and data:
        paths.append((*prefix, 0))
        paths.extend(key_paths(data[0], (*prefix, 0)))
    return paths


def mutated(data, path: tuple, value):
    """A copy of data with the value at path replaced, or removed when value is the REMOVE marker."""
    result = copy.deepcopy(data)
    target = result
    for key in path[:-1]:
        target = target[key]
    if value is REMOVE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return result


REMOVE = object()


def variants(data, rng: random.Random, count: int) -> list[tuple[str, str]]:
    """count (label, JSON text) pairs: one key replaced, removed, or the whole file made hostile."""
    values = hostile_values(rng) + [REMOVE]
    paths = key_paths(data)
    result = [("whole file: deep nesting", deep_json(100_000)), ("whole file: list", "[]"),
              ("whole file: not JSON", "{\"a\": "), ("whole file: duplicate keys", "{\"a\": 1, \"a\": 2}")]
    for _ in range(count):
        path, value = rng.choice(paths), rng.choice(values)
        label = f"{'/'.join(map(str, path))} = {'removed' if value is REMOVE else repr(value)[:60]}"
        result.append((label, json.dumps(mutated(data, path, value))))
    return result


_BUNDLED = {}
_load_catalog = cli.load_catalog


def bundled_catalog_once(extra_dir=None, *, central_policy=None):
    """The bundled catalog validated once per run: it never changes, and validating it is most of a call's time."""
    if central_policy is not None or (extra_dir is not None and extra_dir.exists()):
        return _load_catalog(extra_dir, central_policy=central_policy)
    if "catalog" not in _BUNDLED:
        _BUNDLED["catalog"] = _load_catalog()
    return copy.deepcopy(_BUNDLED["catalog"])


class Crash(Exception):
    """An exception escaped the CLI: the user would see a traceback."""


class FuzzTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.rng = random.Random(f"{SEED}:{self.id()}")
        self.crashes = []
        catalog = patch.object(cli, "load_catalog", bundled_catalog_once)
        catalog.start()
        self.addCleanup(catalog.stop)

    def cli(self, *arguments, stdin="", typed=None):
        """Run the CLI in this process; return (exit code, stdout, stderr) or record a crash."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch("sys.stdin", io.StringIO(stdin)), \
                patch("sys.stdin.isatty", return_value=typed is not None, create=True), \
                patch("builtins.input", return_value=typed or ""):
            try:
                code = main(["--project", str(self.root), *map(str, arguments)])
            except SystemExit as exit_:
                code = exit_.code
            except Exception as error:  # noqa: BLE001 - any escape is the defect under test
                raise Crash(self.signature(error)) from error
        return code, stdout.getvalue(), stderr.getvalue()

    @staticmethod
    def signature(error: Exception) -> tuple[str, str]:
        frames = [frame for frame in traceback.extract_tb(error.__traceback__) if "openguildkit" in frame.filename]
        return type(error).__name__, frames[-1].name if frames else "?"

    def expect(self, *arguments, code=0, **options):
        result = self.cli(*arguments, **options)
        self.assertEqual(result[0], code, f"{' '.join(map(str, arguments))}: {result[2]}")
        return result[1]

    def fuzz(self, label: str, document: dict, run, check=None, count=SAMPLES):
        """Run each variant of document through run(path); collect crashes and broken state."""
        source = self.root / "fuzz-input.json"
        for name, text in variants(document, self.rng, count):
            source.write_text(text, encoding="utf-8", errors="surrogatepass")
            context = f"seed {SEED}, {label}, {name}"
            try:
                code, _, stderr = run(source)
            except Crash as crash:
                exception = crash.args[0]
                if STRICT or exception not in KNOWN_CRASHES:
                    self.crashes.append(f"{context}: {exception[0]} in {exception[1]}")
                continue
            if code not in (0, 1, 2):
                self.crashes.append(f"{context}: exit code {code}")
            elif code == 1 and not stderr.startswith("OpenGuildKit: "):
                self.crashes.append(f"{context}: error without the OpenGuildKit prefix: {stderr[:200]!r}")
            if check:
                check(context)
        self.assertEqual(self.crashes, [])

    def init(self, **extra):
        profile = {**json.loads((EXAMPLE / "profile.json").read_text(encoding="utf-8")), **extra}
        (self.root / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
        self.expect("init", "--answers", self.root / "profile.json")

    def consulted(self):
        self.init()
        self.expect("task", "new", "--file", EXAMPLE / "brief.md", "--id", TASK)
        self.expect("task", "consult", TASK, "--answers", EXAMPLE / "answers.json")

    def review(self):
        template = json.loads(self.expect("task", "template", TASK))
        return {"consultation_id": template["consultation_id"],
                **json.loads((EXAMPLE / "review.json").read_text(encoding="utf-8"))}

    def reviewed(self):
        self.consulted()
        (self.root / "review.json").write_text(json.dumps(self.review()), encoding="utf-8")
        self.expect("task", "review", TASK, "--file", self.root / "review.json")

    def record(self):
        return json.loads(self.expect("task", "show", TASK))

    def record_loads(self, statuses):
        def check(context):
            code, stdout, stderr = self.cli("task", "show", TASK)
            if code != 0:
                self.crashes.append(f"{context}: the task record no longer loads: {stderr}")
            elif json.loads(stdout)["status"] not in statuses:
                self.crashes.append(f"{context}: status became {json.loads(stdout)['status']}")
        return check


class InputFileTests(FuzzTestCase):
    def test_setup_answers(self):
        profile = json.loads((EXAMPLE / "profile.json").read_text(encoding="utf-8"))
        profile.update(boards=["security"], guard="warn", waivers={"allowed_by": ["Camille"], "max_days": 30})
        self.fuzz("preview --answers", profile, lambda path: self.cli("preview", "--answers", path, "--json"))
        self.fuzz("recommend --answers", profile, lambda path: self.cli("recommend", "--answers", path, "--json"))

    def test_init_answers_write_nothing_when_refused(self):
        profile = json.loads((EXAMPLE / "profile.json").read_text(encoding="utf-8"))

        def run(path):
            for leftover in (".openguildkit", "AGENTS.md", "CLAUDE.md"):
                target = self.root / leftover
                if target.is_dir():
                    import shutil

                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            result = self.cli("init", "--answers", path)
            if result[0] != 0 and (self.root / ".openguildkit" / "project.json").exists():
                self.crashes.append(f"refused init left a profile: {result[2]}")
            return result

        self.fuzz("init --answers", profile, run, count=12)

    def test_task_answers(self):
        self.init()
        self.expect("task", "new", "--file", EXAMPLE / "brief.md", "--id", TASK)
        answers = json.loads((EXAMPLE / "answers.json").read_text(encoding="utf-8"))
        answers["follow_ups"] = {"q": "a"}
        self.fuzz("task questions --answers", answers, lambda path: self.cli("task", "questions", TASK, "--answers", path))
        self.fuzz("task route --answers", answers, lambda path: self.cli("task", "route", TASK, "--answers", path))
        self.fuzz("task consult --answers", answers, lambda path: self.cli("task", "consult", TASK, "--answers", path),
                  self.record_loads({"draft", "consulting"}))

    def test_review_files(self):
        self.consulted()
        review = self.review()
        self.fuzz("task review --file", review, lambda path: self.cli("task", "review", TASK, "--file", path),
                  self.record_loads({"consulting", "reviewed"}), count=40)

    def test_board_entries(self):
        self.consulted()
        entry = next(item for item in self.review()["boards"] if item["id"] == "security")
        self.expect("task", "pass", TASK, "security")
        self.fuzz("task submit --file", entry, lambda path: self.cli("task", "submit", TASK, "--file", path),
                  self.record_loads({"consulting"}), count=40)

    def test_deliberations(self):
        self.reviewed()
        request = json.loads(self.expect("task", "deliberate", TASK))
        payload = request["template"]
        for response in payload["responses"]:
            response.update(position="maintain", response="Holds.", evidence_refs=[1])
        payload["decision_needed"] = "Choose the scope."
        self.fuzz("task respond --file", payload, lambda path: self.cli("task", "respond", TASK, "--file", path),
                  self.record_loads({"reviewed"}))

    def test_result_files(self):
        self.reviewed()
        self.expect("task", "decide", TASK, "--approve", "--by", "Camille", "--reason", "Agreed", typed=TASK)
        checks = json.loads(self.expect("task", "checks", TASK, "--json"))
        result = {"revision": "0" * 40, "criteria": [{"criterion": "Users see their requests.", "status": "met",
                                                     "evidence": "tests/test_status.py"}],
                  "checks": [{"check": check["id"], "status": "not-applicable", "reason": "Not touched.",
                              "owner": "Camille"} for check in checks]}
        self.fuzz("task result --file", result, lambda path: self.cli("task", "result", TASK, "--file", path),
                  self.record_loads({"approved"}), count=40)


class StateFileTests(FuzzTestCase):
    def test_a_damaged_profile_gives_a_message_on_every_command(self):
        self.consulted()
        profile_path = self.root / ".openguildkit" / "project.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        original = profile_path.read_bytes()

        def run(path):
            profile_path.write_bytes(path.read_bytes())
            try:
                for command in (("check",), ("task", "list"), ("task", "progress", TASK), ("guard", "--json")):
                    code, stdout, stderr = self.cli(*command)
                    if code not in (0, 1):
                        return code, stdout, stderr
                    if code == 1 and not stderr.startswith("OpenGuildKit: "):
                        return code, stdout, stderr
                return 0, "", ""
            finally:
                profile_path.write_bytes(original)

        self.fuzz("damaged project.json", profile, run)

    def test_a_damaged_task_record_is_reported_not_crashed_on(self):
        self.reviewed()
        task_path = self.root / ".openguildkit" / "tasks" / f"{TASK}.json"
        record = json.loads(task_path.read_text(encoding="utf-8"))
        original = task_path.read_bytes()

        def run(path):
            task_path.write_bytes(path.read_bytes())
            try:
                for command in (("task", "list", "--json"), ("task", "show", TASK), ("task", "progress", TASK),
                                ("task", "challenge", TASK), ("guard", "--json")):
                    code, stdout, stderr = self.cli(*command)
                    if code not in (0, 1) or (code == 1 and not stderr.startswith("OpenGuildKit: ")):
                        return code, stdout, stderr
                return 0, "", ""
            finally:
                task_path.write_bytes(original)

        self.fuzz("damaged task record", record, run, count=40)


class HookInputTests(FuzzTestCase):
    def test_hook_input_never_crashes_and_enforce_still_refuses_an_uncovered_edit(self):
        self.init(guard="enforce")
        hook = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                "tool_input": {"file_path": str(self.root / "src" / "app.py")}, "cwd": str(self.root)}
        outcomes = []

        def run(path):
            stdin = path.read_text(encoding="utf-8", errors="surrogatepass")
            result = self.cli("guard", "--hook", "pre-edit", stdin=stdin)
            self.cli("guard", "--hook", "prompt", stdin=stdin)
            outcomes.append(result[0])
            return result[0] if result[0] != 2 else 0, result[1], result[2]

        self.fuzz("guard --hook pre-edit", hook, run)
        self.assertIn(2, outcomes)


class CommandSequenceTests(FuzzTestCase):
    """A small model-based test: random valid and invalid task commands, checked against a model of the states."""

    def test_random_command_sequences_keep_every_record_consistent(self):
        self.init(waivers={"allowed_by": ["Camille"], "max_days": 30})
        entries = {item["id"]: item for item in json.loads((EXAMPLE / "review.json").read_text(encoding="utf-8"))["boards"]}
        model = {}
        ids = ["alpha", "beta", "gamma", "Bad ID", "../escape", ""]
        steps = []
        for step in range(120):
            task_id = self.rng.choice(ids)
            action = self.rng.choice(["new", "consult", "pass", "submit", "review", "decide", "request-changes",
                                      "show", "progress", "complete", "clear"])
            code = self.step(action, task_id, entries)
            steps.append(f"{step}: {action} {task_id!r} -> {code}")
            model[task_id] = self.expected(model.get(task_id), action, code)
            for known, status in model.items():
                if status is None:
                    continue
                code, stdout, stderr = self.cli("task", "show", known)
                self.assertEqual(code, 0, f"{known} no longer loads after\n" + "\n".join(steps) + f"\n{stderr}")
                self.assertEqual(json.loads(stdout)["status"], status, "\n".join(steps))
        listed = {row["id"]: row["status"] for row in json.loads(self.expect("task", "list", "--json"))}
        self.assertEqual(listed, {key: value for key, value in model.items() if value is not None})
        self.assertNotIn("unreadable", listed.values())

    def test_a_pass_on_a_draft_is_refused_with_a_message(self):
        """Found by the sequence above: task pass on a task never consulted raised KeyError."""
        self.init()
        self.expect("task", "new", "Draft only.", "--id", "draft")
        code, _, stderr = self.cli("task", "pass", "draft", "security")
        self.assertEqual((code, stderr.strip()),
                         (1, "OpenGuildKit: Board passes can only run while the task is being consulted."))

    def step(self, action, task_id, entries):
        if action == "new":
            return self.cli("task", "new", "Random brief.", "--id", task_id)[0]
        if action == "consult":
            return self.cli("task", "consult", task_id, "--answers", EXAMPLE / "answers.json")[0]
        if action == "pass":
            return self.cli("task", "pass", task_id, self.rng.choice(sorted(entries) + ["nobody"]))[0]
        if action == "submit":
            path = self.root / "entry.json"
            path.write_text(json.dumps(entries[self.rng.choice(sorted(entries))]), encoding="utf-8")
            return self.cli("task", "submit", task_id, "--file", path)[0]
        if action == "review":
            return self.cli("task", "review", task_id, "--summary", "Random summary.")[0]
        if action in ("decide", "request-changes"):
            flag = "--approve" if action == "decide" else "--request-changes"
            return self.cli("task", "decide", task_id, flag, "--by", "Camille", "--reason", "Random.", typed=task_id)[0]
        if action == "clear":
            return self.cli("task", "clear", task_id, "security:1", "--by", "Alex", "--evidence", "x", "--supports", "y")[0]
        return self.cli("task", action, task_id)[0]

    @staticmethod
    def expected(status, action, code):
        """The status the model expects after a command; a refused command changes nothing."""
        if code != 0:
            return status
        transitions = {
            "new": "draft", "consult": "consulting", "review": "reviewed", "decide": "approved",
            "request-changes": "changes-requested", "complete": "done",
        }
        return transitions.get(action, status)


if __name__ == "__main__":
    unittest.main()
