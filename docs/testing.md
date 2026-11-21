# Testing

Every test uses `unittest` from the standard library, runs offline and gives the same result on every run. CI runs the whole suite on Linux, macOS and Windows with Python 3.11 to 3.14 (`python -m unittest discover -s tests` in `.github/workflows/ci.yml`), so each kind below runs there too.

```bash
PYTHONPATH=src python -m unittest discover -s tests          # everything, about 5 minutes
PYTHONPATH=src python -m unittest tests.test_robustness      # one kind
PYTHONPATH=src python -m unittest tests.e2e.test_concurrency.LockTests   # one class
```

On Windows, set `PYTHONPATH` with `set PYTHONPATH=src` (cmd) or `$env:PYTHONPATH="src"` (PowerShell), or install the project with `python -m pip install -e .` and drop it.

## Kinds of tests

Times were measured on a laptop (macOS, Python 3.15) on 21 November 2026; the whole suite (245 tests) took 268 s.

| Kind | Where | What it proves | Time |
| --- | --- | --- | --- |
| Unit | `tests/test_*.py` not listed below | Each module's rules: profile validation, routing, reviews, decisions, results, guard, hosts, catalog records and their sources. | about 50 s |
| End to end | `tests/e2e/test_cli_flow.py`, `test_board_passes.py`, `test_catalog_lock.py`, `test_export_ci.py`, `test_host_rehearsal.py` | The real CLI in a subprocess on a temporary project, from `init` to the handoff, with the request status example as fixture. | about 95 s |
| Every command | `tests/e2e/test_more_commands.py`, `tests/test_cli_coverage.py` | `task deliberate`, `respond`, `blockers`, `clear`, `waive`, `storybook compose` and `deliverable list` through the CLI. The coverage test walks the `argparse` tree and fails when a command or a `task` action has no test that invokes it. | 19 s |
| Concurrency | `tests/e2e/test_concurrency.py` | Real processes on one project: six `task pass` at once all succeed; parallel `task new`, `pass` and `submit` lose nothing; a duplicate task ID is refused once without overwrite; readers running during writes always see whole records; a writer waits for the lock, gives up after `OPENGUILDKIT_LOCK_TIMEOUT` with the usual message, and read commands never wait. Two clones each approve and export a task, then merge without conflict. | 22 s |
| Determinism | `tests/test_determinism.py` | Two fresh projects in different folders, under different `PYTHONHASHSEED` values, get byte-identical `AGENTS.md`, `CLAUDE.md`, profile, catalog lock, `catalog --json`, `recommend --json`, `preview --json`, routing, consultation, board pass, decision brief and deliberation request. Only the random consultation ID is replaced before comparing. | 12 s |
| Robustness | `tests/test_robustness.py` | Seeded random and hostile values (wrong types, random Unicode, control characters, 200 000-character strings, 100 000 levels of nesting, removed keys) at each key of setup answers, task answers, reviews, board entries, deliberations, results, the profile, a task record and hook input. No traceback, errors start with `OpenGuildKit:`, task records still load. A small model-based test runs 120 random valid and invalid `task` commands and checks every record against a model of the statuses. | 24 s |
| Performance | `tests/test_performance.py`, `scripts/bench.py` | Time budgets for the commands that run most often, as ratios to the CLI start, so a slow runner does not fail them and a tenfold slowdown does. | 27 s |
| Upgrade | `tests/test_upgrade.py`, `tests/fixtures/upgrade/` | Projects written by v0.1.0 and v0.16.1 themselves (profile, instructions, ignore file, project lock, draft and approved tasks) pass `check`, `update`, `update --apply`, list and show their tasks, and continue a draft through consultation. | 11 s |

The Node coordinator in `integrations/ci` has its own suite: `cd integrations/ci && npm test`. `tests/e2e/test_export_ci.py` runs the Python side of the cases it shares with that suite.

## Robustness: seeds and known crashes

The robustness tests use a fixed seed, so a failure names the seed, the command and the mutated key, and reproduces. To explore, set another seed:

```bash
OPENGUILDKIT_FUZZ_SEED=7 PYTHONPATH=src python -m unittest tests.test_robustness
```

Crashes already reported elsewhere are listed in `KNOWN_CRASHES` with their issue (#99 for malformed inputs, #95 for the edit hook) and do not fail the run. `OPENGUILDKIT_FUZZ_STRICT=1` fails on them too; remove an entry once its issue is closed. Other seeds still find crashes on task records edited by hand (a challenge or a date replaced by a list or an object): `read_task` checks which keys a record has, not their types.

## Performance numbers

`python scripts/bench.py` prints the times as a table and as JSON (`--json` for JSON only, `--repeat N` for more runs). Each command is a real process; the figure is the median of five runs after one warm-up. "x start" divides by `openguildkit --version` in the same run.

Measured on 21 November 2026 on a laptop (macOS 26, Python 3.15) that was running other test suites at the same time (load average about 10). The last column gives the lowest and highest ratio over three runs of the script.

| Command | Median (s) | x start | x start, 3 runs |
| --- | --- | --- | --- |
| `python -c pass` | 0.025 | 0.1 | 0.1 to 0.2 |
| `openguildkit --version` | 0.184 | 1.0 | 1.0 |
| `catalog --json` | 0.276 | 1.5 | 1.5 to 4.9 |
| `init` | 0.384 | 2.1 | 1.8 to 3.6 |
| `task consult` | 0.460 | 2.5 | 2.3 to 2.8 |
| `task list`, 500 tasks | 0.540 | 2.9 | 2.9 to 5.0 |
| `references security` | 0.322 | 1.8 | 1.8 to 3.0 |
| `guard --hook pre-edit`, 500 tasks | 0.544 | 3.0 | 3.0 to 5.1 |
| `guard --hook prompt`, 500 tasks | 0.552 | 3.0 | 3.0 to 4.9 |
| `detect`, monorepo of 200 packages | 0.459 | 2.5 | 2.1 to 3.2 |
| `init`, monorepo of 200 packages | 0.581 | 3.2 | 3.2 to 3.9 |

Each budget in `tests/test_performance.py` is at least three times the highest ratio seen, so load on the machine passes, and at most ten times the lowest, so a tenfold slowdown fails. A run over budget is measured once more before the test fails. `--version` itself must stay under 25 times a bare `python -c pass`.

Where the time goes: every command validates the bundled catalog twice (`load_catalog` for the live and the bundled records, about 70 ms each), and `guard --hook pre-edit` reads every task record to find approved ones, so the hook that runs before each agent edit grows with the number of tasks.

## Not covered

- Windows behaviour is covered by CI only: the lock on Windows (`msvcrt.locking`) has not been run by hand, and the approval, waiver and authorization prompts are tested through a pseudo-terminal on Linux and macOS only.
- The concurrency tests run on one machine. A project on a network file system, where file locks may not hold, is not tested.
- No test runs an agent host (Claude Code, Codex) for real; host settings and hooks are tested through the files they read and the input they send.
- The robustness tests do not cover `.claude/settings.json`, `.codex/hooks.json`, market dossiers or the share register, whose known crashes are listed in #99.
- Performance is measured with up to 500 tasks and 200 packages, not with a project's full history over years.
- Upgrade fixtures cover the first and the last release before the catalog lock; releases in between are not kept.
