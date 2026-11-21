"""Time the CLI commands that run most often, and print the times as a table and as JSON.

    python scripts/bench.py [--repeat N] [--json]

Each command runs as a real process, as an agent or a hook runs it, in projects
built in a temporary folder: the request status example, the same project with
500 tasks, and a monorepo of 200 packages. The time of each command is the
median of N runs; "x start" divides it by the time of openguildkit --version,
so the figure stays comparable between a fast laptop and a slow CI runner.
tests/test_performance.py runs the same measures with budgets.
"""

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
sys.path.insert(0, str(SOURCE))

from openguildkit.storage import write_json  # noqa: E402

EXAMPLE = ROOT / "examples" / "request-status"
TASK = "request-status"
TASKS = 500
PACKAGES = 200
ENV = {
    **os.environ,
    "PYTHONPATH": os.pathsep.join(filter(None, [str(SOURCE), os.environ.get("PYTHONPATH")])),
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}


def cli(project: Path, *args, stdin: str = "") -> float:
    """Run one command and return its wall time in seconds; a failure stops the benchmark."""
    command = [sys.executable, "-m", "openguildkit", "--project", str(project), *map(str, args)]
    started = time.perf_counter()
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, encoding="utf-8", env=ENV,
                            timeout=600)
    elapsed = time.perf_counter() - started
    if result.returncode not in (0, 2) or (result.returncode == 2 and args[:1] != ("guard",)):
        raise RuntimeError(f"openguildkit {' '.join(map(str, args))} failed: {result.stderr}")
    return elapsed


def interpreter() -> float:
    """Wall time of a bare interpreter start, the floor under every command."""
    started = time.perf_counter()
    subprocess.run([sys.executable, "-c", "pass"], check=True, env=ENV, timeout=600)
    return time.perf_counter() - started


def example_project(folder: Path, guard: str = "off") -> Path:
    folder.mkdir(parents=True)
    profile = {**json.loads((EXAMPLE / "profile.json").read_text(encoding="utf-8")), "guard": guard}
    write_json(folder / "profile.json", profile)
    cli(folder, "init", "--answers", folder / "profile.json")
    return folder


def many_tasks(folder: Path) -> Path:
    """The example project with TASKS records: half drafts, half consulted copies of the example task."""
    project = example_project(folder, guard="enforce")
    cli(project, "task", "new", "--file", EXAMPLE / "brief.md", "--id", TASK)
    cli(project, "task", "consult", TASK, "--answers", EXAMPLE / "answers.json")
    tasks = project / ".openguildkit" / "tasks"
    consulted = json.loads((tasks / f"{TASK}.json").read_text(encoding="utf-8"))
    draft = {key: consulted[key] for key in ("schema_version", "brief", "created_at")}
    for index in range(TASKS - 1):
        task_id = f"task-{index:04d}"
        record = {**consulted, "id": task_id} if index % 2 else {**draft, "id": task_id, "status": "draft", "rounds": []}
        write_json(tasks / f"{task_id}.json", record)
    return project


def monorepo(folder: Path) -> Path:
    """PACKAGES packages over three levels, each with a manifest and a few source files."""
    folder.mkdir(parents=True)
    for index in range(PACKAGES):
        package = folder / ("apps" if index % 2 else "packages") / f"group-{index % 10}" / f"pkg-{index:03d}"
        (package / "src").mkdir(parents=True)
        if index % 3:
            (package / "package.json").write_text(json.dumps({
                "name": f"pkg-{index}", "dependencies": {"react": "^18.3.1", "next": "14.2.0"},
                "devDependencies": {"typescript": "5.6.3", "vitest": "2.1.0"}}), encoding="utf-8")
        else:
            (package / "pyproject.toml").write_text(
                f'[project]\nname = "pkg-{index}"\ndependencies = ["fastapi>=0.110", "psycopg>=3.1"]\n', encoding="utf-8")
        for name in ("index.ts", "util.ts", "README.md"):
            (package / "src" / name).write_text("export {};\n", encoding="utf-8")
        (package / "node_modules" / "ignored").mkdir(parents=True)
    return folder


def edit_hook(project: Path) -> str:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": str(project),
                       "tool_input": {"file_path": str(project / "src" / "app.py")}})


def scenarios(base: Path) -> list[tuple[str, callable]]:
    """(name, run) pairs; each run returns the wall time of one command."""
    plain = example_project(base / "plain")
    big = many_tasks(base / "many-tasks")
    repo = monorepo(base / "monorepo")
    counter = iter(range(10 ** 6))

    def fresh_init():
        folder = base / "init" / str(next(counter))
        folder.mkdir(parents=True)
        return cli(folder, "init", "--answers", EXAMPLE / "profile.json")

    def consult():
        task_id = f"consult-{next(counter)}"
        cli(plain, "task", "new", "--file", EXAMPLE / "brief.md", "--id", task_id)
        return cli(plain, "task", "consult", task_id, "--answers", EXAMPLE / "answers.json")

    def init_monorepo():
        shutil.rmtree(repo / ".openguildkit", ignore_errors=True)
        for name in ("AGENTS.md", "CLAUDE.md"):
            (repo / name).unlink(missing_ok=True)
        return cli(repo, "init", "--answers", EXAMPLE / "profile.json")

    return [
        ("python -c pass", interpreter),
        ("start (--version)", lambda: cli(plain, "--version")),
        ("catalog --json", lambda: cli(plain, "catalog", "--json")),
        ("init", fresh_init),
        ("task consult", consult),
        (f"task list ({TASKS} tasks)", lambda: cli(big, "task", "list")),
        ("references search", lambda: cli(plain, "references", "security")),
        (f"guard --hook pre-edit ({TASKS} tasks)", lambda: cli(big, "guard", "--hook", "pre-edit", stdin=edit_hook(big))),
        (f"guard --hook prompt ({TASKS} tasks)", lambda: cli(big, "guard", "--hook", "prompt", stdin="{}")),
        (f"detect (monorepo, {PACKAGES} packages)", lambda: cli(repo, "detect", "--json")),
        (f"init (monorepo, {PACKAGES} packages)", init_monorepo),
    ]


def measure(repeat: int = 3) -> dict:
    """Median seconds per scenario, the runs, and the ratio to the start time."""
    base = Path(tempfile.mkdtemp(prefix="ogk-bench-")).resolve()
    try:
        results = {}
        for name, run in scenarios(base):
            run()  # warm the file cache and the bytecode
            runs = [run() for _ in range(repeat)]
            results[name] = {"median": statistics.median(runs), "runs": runs}
        start = results["start (--version)"]["median"]
        for item in results.values():
            item["x_start"] = item["median"] / start
        return results
    finally:
        shutil.rmtree(base, ignore_errors=True)


def report(results: dict) -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "results": {name: {"median_s": round(item["median"], 3), "x_start": round(item["x_start"], 1)}
                    for name, item in results.items()},
    }


def table(results: dict) -> str:
    width = max(map(len, results))
    lines = [f"{'command':<{width}}  median s  x start", f"{'-' * width}  --------  -------"]
    lines += [f"{name:<{width}}  {item['median']:8.3f}  {item['x_start']:7.1f}" for name, item in results.items()]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=5, help="Runs per command (default 5).")
    parser.add_argument("--json", action="store_true", help="Print only the JSON report.")
    args = parser.parse_args()
    results = measure(args.repeat)
    if not args.json:
        print(table(results))
        print()
    print(json.dumps(report(results), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
