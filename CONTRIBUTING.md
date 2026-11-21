# Contributing to OpenGuildKit

OpenGuildKit is useful only if its catalog reflects how practitioners actually work. If you know a discipline, a standard or a sector, you can contribute without writing Python.

## Ways to contribute

| You know... | Contribute |
|---|---|
| A published specification or useful source | A [reference entry](docs/references.md) in `src/openguildkit/registry/references/` |
| A recurring stack or team method | A [pattern](docs/patterns.md) linking the context to versioned rule packs |
| A standard, regulation or convention | A rule pack in `src/openguildkit/registry/packs/` |
| A discipline (security, design, finance...) | A new board or better questions for an existing one |
| A sector (banking, health, retail...) | A sector record in `src/openguildkit/registry/sectors/` |
| A coding agent | An integration in `integrations/` |
| Python | CLI features, tests, bug fixes |

Before large work, open an issue with the matching template so a steward or maintainer can confirm the scope. [Your first contribution](docs/first-contribution.md) walks through one change from the fork to the merged pull request.

## Open a pull request

For larger changes, use an issue to discuss scope. Fork the repository, create a branch for the change and open a pull request against `main`. Use `Closes #123` for the issue the PR resolves, or `Refs #123` for related work. Include the part of the catalog or workflow you exercised.

GitHub labels PRs from their changed files and requests a review from the owners in `.github/CODEOWNERS`. The `contribution-checks` status combines the test matrix and package build. Merges to `main` refresh a draft of the next release notes. Maintainers publish versions with the [release steps](GOVERNANCE.md#releases).

Everything you add to the repository, catalog content included (packs, references, patterns, boards, sectors and tools), is published under the MIT License in [LICENSE](LICENSE), like the code. Opening a pull request means you agree to that and that you have the right to contribute the content. Write rules and descriptions in your own words and never paste the text of a standard: only text you may license can be published under the MIT License.

## Set up

```bash
git clone https://github.com/Sandbox-podcast/OpenGuildKit.git
cd OpenGuildKit
python -m pip install -e .
python -m unittest discover -s tests
python -m openguildkit catalog
python -m openguildkit references
python -m openguildkit patterns
```

No runtime dependency. Tests use `unittest` from the standard library. [Testing](docs/testing.md) lists the kinds of tests (unit, end to end, concurrency, determinism, robustness, performance, upgrade), how to run each one and what they do not cover; `python scripts/bench.py` prints the time of the main commands.

## Adding a pack

1. Check [catalog coverage](docs/coverage.md) for your discipline: the brains with few packs and the documentary references that no pack applies yet show where a pack is missing.
2. Copy an existing pack, for example `src/openguildkit/registry/packs/api-design.json`, to `src/openguildkit/registry/packs/<your-id>.json`.
3. Set `version` to `1.0.0`. Start the description with the context the pack applies to, such as "For a service in production: ...".
4. Write each rule as an `instruction` the agent can follow and a `verification` a reviewer can check. See [writing good records](docs/catalog-format.md#writing-good-records).
5. Link the official source for the pack. Paraphrase; never paste text from a paid standard.
6. Reach the pack from a pattern for the roles that use it, and add the pack to the `packs` of the references it applies.
7. Run `python -m openguildkit catalog`, `python scripts/coverage.py` to refresh the coverage page, and the tests.
8. In the pull request, say which projects would use the pack and how you tested the rules on a real repository.

## Adding a board

Boards should map to a real discipline with its own questions. Give it 3 expertises, 3 questions every task should answer, and triggers in English plus at least one other language. Check that your triggers do not fire on common words: add briefs that must and must not select the board to `tests/routing_cases.json`, which CI runs on every change.

## Adding a sector

A sector record lists what an agent must ask or check in that industry: roles, data, who approves which decision, regulators. Write context items as instructions, not descriptions of the industry.

## Quality bar for the catalog

A catalog pull request is merged when:

- the JSON validates (CI runs it on Linux, macOS and Windows);
- every rule has a verification a reviewer could actually perform;
- sources are official or widely recognized, use HTTPS and link the canonical page without tracking parameters, with one address per document;
- nothing claims compliance or certification;
- a steward of the domain approved it (see [GOVERNANCE.md](GOVERNANCE.md)).

## Reviewing source links

The scheduled workflow `Check catalog links` runs every Monday and on demand. It runs `python3 -I scripts/check_links.py --out link-report`, uploads the dated `links-YYYY-MM-DD.md` and `.json` report as the `link-report` artifact, and fails only when a link is dead. It also opens the homepage, the API documentation and the source of each MCP server in the tool registry. The script reads the bundled catalog only: catalogs that projects keep in `.openguildkit/catalog/` stay in those projects and are never fetched.

For each URL, the report gives its category and the records that cite it: references with their edition and the packs that apply them, packs with their version and rule IDs, patterns, boards and sectors. A maintainer reviews it as follows.

- **Dead:** find the page that replaces it on the publisher's site, fetch it, and check its status and title. Replace the URL and the title in every record the report lists, so the document keeps one address.
- **Redirected:** use the target when it is the publisher's new canonical page. Leave aliases such as `latest` when the catalog means to follow the current version.
- **To recheck:** open the page in a browser. Some publishers (ISO, W3C, PMI, EUR-Lex) answer scripts with 403 or 202, and GitHub sometimes answers 503 under load; keep the URL when the page works.
- **New edition:** the report lists the packs and rules that cite the old edition. Decide with the domain steward whether the rules change, bump the pack version (patch for a source or wording change, minor or major when rules change) and update the reference's `edition`. Projects see the change in `openguildkit update` before they apply it.

A maintainer validates every replacement in the pull request that makes it. Run the script locally with the same command before proposing one.

## Code contributions

- The agent skill and subagent live in `src/openguildkit/agent_files/` and are copied in `integrations/`; edit both, the tests check that they match.

- Python 3.11+, standard library only at runtime.
- Keep the CLI offline. It must never call a model provider or the network.
- Add unit tests in `tests/` and, for a new command, an end-to-end test in `tests/e2e/`: `tests/test_cli_coverage.py` fails when a command has no test that runs it.
- Keep messages short and actionable, prefixed `OpenGuildKit:` on errors.
- Write code, rule packs and contribution docs in English. Reference entries can keep their original language. The French README is maintained alongside the English one.

## Pull requests

- One topic per pull request.
- Describe what changes for a project that already uses OpenGuildKit. A pack change invalidates pending approvals in those projects; say so.
- Fill in the pull request template.

## Conduct

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).
