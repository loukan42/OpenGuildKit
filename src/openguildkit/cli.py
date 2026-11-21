"""Command-line interface for OpenGuildKit."""

import argparse
import json
import re
import sys
from pathlib import Path

from .catalog import load_catalog
from .ci import check_export, read_policy_pack
from .decisions import NAMESPACE, approval_policy, check_decision, decision_payload, git_revision, payload_text, verify_signature
from . import alerts_cli
from .alerts import blocking_alerts, load_register
from .deliverables import draft_problems, launch_notice, load_deliverables, new_draft, utm_link
from .brains import (
    brain_knowledge, brief_knowledge, consultation_context, knowledge_gaps, reading_list_note, write_brain_skeleton,
)
from .detect import components_for_profile, detect_stack
from .publishing import add_share, export_docs, project_overview, revoke_share, share_status
from . import tools_cli
from .tools import connected_tools, load_tools
from . import market_cli
from .market import load_dossier
from .market import scope_entry as market_scope_entry
from .storybook import STORYBOOK_VERSION
from .storybook import run as storybook_command
from .storybook import scope_entry as storybook_scope_entry
from .integrations import install_agent_files
from .guard import NO_TASK, STALE_DAYS, ci_guard, covering_approvals, export_decision, guard_mode, run_hook
from .results import chaosfolk_checks, compare_runs, completion_problems, expected_checks, missing_scenarios, result_template, run_command_checks
from .hosts import apply_host_settings, approval_warning, host_controls
from .lock import (
    package_version,
    apply_lock,
    build_lock,
    catalog_changes,
    lock_is_current,
    read_lock,
    unshipped_records,
    update_target,
    write_lock,
)
from .patterns import patterns_to_review, role_choices
from .project import (
    check_instructions,
    init_questions,
    initialize,
    load_profile,
    profile_recommendations,
    profile_fingerprint,
    project_root,
    resolved_profile,
    rule_conflicts,
    rule_plan,
    state_path,
    sync_instructions,
    validate_profile,
)
from .references import find_references, load_references, with_pattern_references
from .storage import json_text, project_lock, read_json, write_json
from .tasks import (
    timestamp,
    list_tasks,
    complete_task,
    record_result,
    resolve_blocker,
    record_deliberation,
    assemble_review,
    pass_progress,
    start_pass,
    submit_pass,
    ACTIONS,
    record_authorization,
    create_task,
    read_task,
    record_consultation,
    record_decision,
    record_review,
    require_current_scope,
    task_path,
)
from .workflow import blocker_list, board_brief, challenge_report, decision_summary, deliberation_request, handoff, open_points, pending_removals, select_boards, task_questions, validate_answers, validate_review, verdict_lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openguildkit", description="Project rules and specialist reviews for AI-assisted work.")
    parser.add_argument("--version", action="version", version=f"openguildkit {package_version()}")
    parser.add_argument("--project", type=Path, help="Project root (defaults to the closest configured ancestor).")
    parser.add_argument("--policy-repo", type=Path, help="Local Git repository with the central export rule.")
    parser.add_argument("--policy-commit", help="Full Git SHA of the central policy commit.")
    commands = parser.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog", help="List rule packs, boards and sectors.")
    catalog.add_argument("--json", action="store_true", help="Print the complete catalog as JSON.")
    references = commands.add_parser("references", help="Browse the reference library.")
    references.add_argument("query", nargs="?", help="Search reference names and descriptions.")
    references.add_argument("--family", help="Show references from one family ID.")
    references.add_argument("--id", help="Show one reference and its sources.")
    references.add_argument("--json", action="store_true", help="Print matching references as JSON.")
    patterns = commands.add_parser("patterns", help="List stack patterns and work methods.")
    patterns.add_argument("--role", help="Filter by role ID.")
    patterns.add_argument("--id", help="Show a pattern, its match and its rule packs.")
    patterns.add_argument("--json", action="store_true", help="Print matching patterns as JSON.")
    preview = commands.add_parser("preview", help="Show the rule packs, their origin and scope before writing them.")
    preview.add_argument("--answers", type=Path, help="Preview setup answers instead of the saved profile.")
    preview.add_argument("--json", action="store_true", help="Print the plan as JSON.")
    detect = commands.add_parser("detect", help="Propose components and technologies from the repository's manifests.")
    detect.add_argument("--json", action="store_true", help="Print the components and their evidence as JSON.")
    recommend = commands.add_parser("recommend", help="Preview patterns for a project context.")
    recommend.add_argument("--answers", type=Path, help="Read sector, stack and roles from a JSON file.")
    recommend.add_argument("--json", action="store_true", help="Print matches and suggestions as JSON.")
    brains = commands.add_parser("brains", help="List the specialist review boards.")
    brains.add_argument("--id", help="Show a brain's specialist lenses and review criteria.")
    brains.add_argument("--json", action="store_true", help="Print the board definitions as JSON.")
    brains.add_argument("--check", action="store_true", help="Fail when a brain has no method or no reference.")
    brains.add_argument("--new", metavar="ID", help="Write the skeleton of a project brain in .openguildkit/catalog.")
    init = commands.add_parser("init", help="Initialize a project profile and agent instructions.")
    init_mode = init.add_mutually_exclusive_group()
    init_mode.add_argument("--questions", action="store_true", help="Print the setup questionnaire as JSON.")
    init_mode.add_argument("--answers", type=Path, help="Read setup answers from a JSON file.")
    commands.add_parser("sync", help="Update the managed instructions from the project profile.")
    guard = commands.add_parser("guard", help="Say whether an approved task covers the work, for hooks and CI.")
    guard_mode = guard.add_mutually_exclusive_group()
    guard_mode.add_argument("--hook", choices=("prompt", "pre-edit"), help="Run as a host hook; reads the hook input on stdin.")
    guard_mode.add_argument("--ci", action="store_true", help="Check the decisions exported for the changes since --base.")
    guard.add_argument("--base", default="origin/main", help="Base revision for --ci (default origin/main).")
    guard.add_argument("--json", action="store_true", help="Print the result as JSON.")
    deliverable = commands.add_parser("deliverable", help="Start and check a business deliverable from an approved task.")
    deliverable_actions = deliverable.add_subparsers(dest="deliverable_action", required=True)
    deliverable_actions.add_parser("list", help="List the deliverable templates.")
    show_deliverable = deliverable_actions.add_parser("show", help="Show a template: inputs, approvals and checks.")
    show_deliverable.add_argument("id")
    new_deliverable = deliverable_actions.add_parser("new", help="Write a draft for an approved task.")
    new_deliverable.add_argument("id")
    new_deliverable.add_argument("--task", required=True, help="Approved task the deliverable comes from.")
    new_deliverable.add_argument("--tool", required=True, help="Tool the team uses, from the profile's approved tools.")
    new_deliverable.add_argument("--output", type=Path, required=True, help="Markdown file to write.")
    check_deliverable = deliverable_actions.add_parser("check", help="List what keeps a draft from review.")
    check_deliverable.add_argument("path", type=Path)
    utm = deliverable_actions.add_parser("utm", help="Print a landing URL tagged with the campaign's UTM parameters.")
    utm.add_argument("--url", required=True, help="https address of the landing page.")
    for name in ("campaign", "source", "medium"):
        utm.add_argument(f"--{name}", required=True, help=f"utm_{name} value agreed with the analytics owner.")
    utm.add_argument("--content", help="utm_content value, to tell links of the same campaign apart.")
    utm.add_argument("--term", help="utm_term value, for paid search keywords.")
    docs = commands.add_parser("docs", help="Export documentation of completed tasks and track who it is shared with.")
    docs_actions = docs.add_subparsers(dest="docs_action", required=True)
    export = docs_actions.add_parser("export", help="Write Reference, Learn and Blog pages for Documentalist.")
    export.add_argument("--tasks", required=True, help="Comma-separated IDs of completed tasks to document.")
    export.add_argument("--output", type=Path, required=True, help="Folder to write, for example docs-site.")
    export.add_argument("--version", dest="doc_version", required=True, help="Project version the pages describe.")
    export.add_argument("--sections", default="project,reference,learn,blog", help="Sections to write.")
    export.add_argument("--openapi", type=Path, help="OpenAPI file to place next to the pages.")
    share = docs_actions.add_parser("share", help="Record, list, revoke or check the shares of a deliverable.")
    share.add_argument("share_action", choices=("add", "list", "revoke", "check"))
    share.add_argument("--deliverable", help="Page or folder shared (add).")
    share.add_argument("--recipient", action="append", default=[], help="Recipient email address (add, repeatable).")
    share.add_argument("--until", help="Last day of access, YYYY-MM-DD (add).")
    share.add_argument("--portal", help="https address of the Documentalist portal (add).")
    share.add_argument("--id", dest="share_id", help="Share ID (revoke).")
    share.add_argument("--by", help="Owner of the share (add, revoke).")
    share.add_argument("--json", action="store_true", help="Print the shares as JSON.")
    tools_cli.add_parser(commands)
    alerts_cli.add_parser(commands)
    market_cli.add_parser(commands)
    storybook = commands.add_parser(
        "storybook", help="Compose the company Storybook, record its stories and extract a design system.")
    storybook_actions = storybook.add_subparsers(dest="action", required=True)
    compose = storybook_actions.add_parser(
        "compose", help="Add a published Storybook to the refs file that .storybook/main imports.")
    compose.add_argument("ref", help="Ref ID, such as company; its stories are cited as storybook:REF_STORY_ID.")
    compose.add_argument("--url", help="URL of the published Storybook.")
    compose.add_argument("--title", help="Title shown in the Storybook sidebar.")
    compose.add_argument("--remove", action="store_true", help="Remove this ref from the refs file.")
    compose.add_argument("--config-dir", default=".storybook", help="Storybook configuration folder (default .storybook).")
    sb_import = storybook_actions.add_parser(
        "import", help="Compare Storybook index.json files, report what to map and record the known stories.")
    sb_import.add_argument("--project-index", type=Path,
                           help="The project's index.json or storybook-static folder (default storybook-static).")
    sb_import.add_argument("--company-index", type=Path, help="The company Storybook's index.json or static folder.")
    sb_import.add_argument("--ref", default="company", help="Ref ID of the company Storybook (default company).")
    sb_import.add_argument("--library", type=Path, help="package.json of the company component library.")
    sb_import.add_argument("--package", type=Path, help="The project's package.json (default package.json).")
    sb_extract = storybook_actions.add_parser(
        "extract", help="Inventory the components, variants and style tokens of an existing React application.",
        description="Inventory the components, variants and style tokens of an existing React application. "
                    "Supported: React function components (function declarations, arrow functions, forwardRef and "
                    "memo) in .tsx and .jsx files, their props, literal defaults and string-union prop types, the "
                    "export aliases and re-exports of .ts and .js index files, and CSS and SCSS stylesheets. "
                    "Not supported: class components, Vue, Angular and Svelte components, CSS-in-JS and utility "
                    "classes such as Tailwind's. The sources are read with text patterns, not a full parser: "
                    "check the inventory before proposing.")
    sb_extract.add_argument("--src", type=Path, action="append", required=True,
                         help="Source folder or file to scan, such as a screen; repeat to select several.")
    sb_extract.add_argument("--preview", type=Path, action="append", default=[],
                         help="Screenshot the team may reuse; recorded with its hash, never analyzed.")
    sb_extract.add_argument("--output", type=Path, help="Inventory file (default .openguildkit/storybook/inventory.json).")
    sb_propose = storybook_actions.add_parser(
        "propose", help="Write the proposals to validate from the inventory, keeping the decisions already made.")
    sb_propose.add_argument("--inventory", type=Path, help="Inventory file (default .openguildkit/storybook/inventory.json).")
    sb_propose.add_argument("--proposals", type=Path, help="Proposals file (default .openguildkit/storybook/proposals.json).")
    sb_generate = storybook_actions.add_parser(
        "generate", help="Write tokens, stories and an index for the validated proposals; never overwrite edits.")
    sb_generate.add_argument("--proposals", type=Path, help="Proposals file (default .openguildkit/storybook/proposals.json).")
    sb_generate.add_argument("--out", type=Path, help="Output folder (default src/design-system).")
    sb_generate.add_argument("--framework", default="@storybook/react-vite", help="Storybook framework package for the types.")
    for storybook_action in (compose, sb_import, sb_extract, sb_propose, sb_generate):
        storybook_action.add_argument("--json", action="store_true", help="Print the result as JSON.")
    storybook.epilog = (f"Create the project Storybook with npm create storybook@{STORYBOOK_VERSION}. "
                        "storybook compose prints a minimal .storybook/main.ts when the project has none.")
    agent = commands.add_parser("integrations", help="Install the agent skill and subagent of this version.")
    agent.add_argument("--host", required=True, choices=("claude-code", "codex"), help="Agent to configure.")
    agent.add_argument("--user", action="store_true", help="Install in the home folder for every project.")
    agent.add_argument("--force", action="store_true", help="Replace modified files, keeping a .previous copy.")
    host = commands.add_parser("host", help="Show or apply the rules the host agent can enforce.")
    host.add_argument("mode", choices=("check", "apply"), help="check reports each rule; apply writes the host settings.")
    host.add_argument("--json", action="store_true", help="Print the report as JSON.")
    update = commands.add_parser("update", help="Show what the installed catalog would change and adopt it.")
    update.add_argument("--apply", action="store_true", help="Adopt the installed catalog's records for this project.")
    update.add_argument("--json", action="store_true", help="Print the changes as JSON.")
    commands.add_parser("check", help="Check that the instructions match the project profile.")
    ci = commands.add_parser("ci-check", help="Evaluate the central export rule on an exact Git commit.")
    ci.add_argument("--target-repo", type=Path, required=True, help="Local Git repository with the target commit.")
    ci.add_argument("--commit", required=True, help="Full Git SHA of the target commit.")

    task = commands.add_parser("task", help="Frame a task, record advice and approve a handoff.")
    actions = task.add_subparsers(dest="action", required=True)
    new = actions.add_parser("new", help="Create a task draft.")
    new.add_argument("brief", nargs="?", help="Short description of the work.")
    new.add_argument("--file", type=Path, help="Read the brief from a UTF-8 Markdown or text file.")
    new.add_argument("--id", help="Use a readable lowercase task ID.")
    new.add_argument("--follows", metavar="TASK", help="Link the task to an earlier task's decision.")
    listing = actions.add_parser("list", help="List the project's tasks with their status and next open point.")
    listing.add_argument("--status", help="Only tasks in this status.")
    listing.add_argument("--owner", help="Only tasks decided by this person.")
    listing.add_argument("--since", help="Created on or after YYYY-MM-DD.")
    listing.add_argument("--until", help="Created on or before YYYY-MM-DD.")
    listing.add_argument("--json", action="store_true", help="Print the tasks as JSON.")
    descriptions = {
        "questions": "Print the task questionnaire as JSON.",
        "route": "Preview the boards a consultation would select, and why.",
        "consult": "Save task answers and prepare the board consultation.",
        "brief": "Print the saved consultation for the host agent.",
        "template": "Print the review template as JSON.",
        "pass": "Start one board's independent pass and print what it needs.",
        "submit": "Record one board's review entry from its pass.",
        "progress": "Show which board passes are done, failed, started or missing.",
        "review": "Record the completed board reviews.",
        "challenge": "Print the decision brief and each board's challenge.",
        "deliberate": "Print the objections between boards and the response template.",
        "blockers": "List the review's blockers and how each was resolved.",
        "clear": "Record the evidence that clears a blocker.",
        "waive": "Waive a blocker within the project's waiver policy (needs a terminal).",
        "respond": "Record the boards' answers to each other's objections.",
        "decide": "Record a human decision.",
        "handoff": "Print the approved implementation handoff, or the decision record of a decision task.",
        "decision-payload": "Print the exact decision an approver signs in a project that requires signed approvals.",
        "show": "Print the task record as JSON.",
        "checks": "List the checks the approved task must report.",
        "verify": "Run the project's check commands and record their results.",
        "result": "Record the delivered revision, criteria and check outcomes.",
        "complete": "Declare the task done once mandatory checks passed with evidence.",
        "runs": "Compare the last two ChaosFolk replays of the task.",
        "alerts": "Import dependency alerts for the task into the project register and review them.",
        "export": "Write a shareable decision record to .openguildkit/decisions for the pull request.",
        "authorize": "Record a person's decision on an external action outside the authorized scope.",
    }
    for action, description in descriptions.items():
        command = actions.add_parser(action, help=description)
        command.add_argument("id", help="Task ID.")
        if action in ("consult", "questions", "route"):
            command.add_argument("--answers", type=Path, help="Read task answers from a JSON file.")
        if action == "consult":
            command.add_argument("--market", metavar="DOSSIER", help="Attach a market dossier from .openguildkit/market/.")
        if action in ("consult", "route"):
            command.add_argument("--add", action="append", default=[], metavar="BOARD", help="Add a board to the consultation.")
            command.add_argument("--remove", action="append", default=[], metavar="BOARD", help="Remove a proposed board.")
            command.add_argument("--reason", help="Why the boards were added or removed.")
            command.add_argument("--delegate", action="append", default=[], metavar="BOARD=NAME",
                                 help="Name the person who reviews a required board the project has not enabled.")
        if action == "route":
            command.add_argument("--json", action="store_true", help="Print the selection as JSON.")
        if action == "review":
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--file", type=Path, help="Completed review JSON.")
            source.add_argument("--summary", help="Assemble the review from completed board passes with this summary.")
        if action == "pass":
            command.add_argument("board", help="Board ID.")
        if action in ("pass", "submit"):
            command.add_argument("--mode", choices=("subagent", "sequential"), help="How the host runs this pass.")
        if action in ("clear", "waive"):
            command.add_argument("blocker", help="Blocker ID such as security:1 (see task blockers).")
            command.add_argument("--by", required=True, help="Person who resolves the blocker.")
        if action == "clear":
            command.add_argument("--evidence", required=True, help="Path, document or link that clears it.")
            command.add_argument("--supports", required=True, help="What the evidence establishes.")
        if action == "waive":
            command.add_argument("--reason", required=True, help="Why the risk is accepted.")
            command.add_argument("--scope", required=True, help="What the waiver covers.")
            command.add_argument("--until", required=True, help="Last day of the waiver, YYYY-MM-DD.")
        if action == "blockers":
            command.add_argument("--json", action="store_true", help="Print the blockers as JSON.")
        if action == "export":
            command.add_argument("--output", type=Path, help="Write the shareable record to this file instead.")
        if action == "checks":
            command.add_argument("--json", action="store_true", help="Print the checks as JSON.")
        if action == "result":
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--file", type=Path, help="Result JSON.")
            source.add_argument("--template", action="store_true", help="Print a result file to fill in.")
        if action == "verify":
            command.add_argument("--chaosfolk", type=Path, metavar="SUMMARY",
                                 help="Record a ChaosFolk regression summary.json instead of running the check commands.")
            command.add_argument("--tool-version", help="ChaosFolk version or commit that produced the summary.")
        if action == "alerts":
            command.add_argument("--import", dest="import_file", type=Path, metavar="REPORT",
                                 help="npm audit, pip-audit, OSV-Scanner or Dependabot JSON report.")
            command.add_argument("--set", dest="alert_id", metavar="ALERT", help="Alert ID to review.")
            command.add_argument("--link", metavar="ALERT", help="Link an alert of the register to the task.")
            command.add_argument("--status", choices=alerts_cli.STATUS_CHOICES, help="Review status for --set.")
            command.add_argument("--by", help="Reviewer's name for --set.")
            command.add_argument("--reason", default="", help="Reason, required to dismiss.")
            command.add_argument("--json", action="store_true", help="Print the alerts as JSON.")
        if action == "runs":
            command.add_argument("--json", action="store_true", help="Print the comparison as JSON.")
        if action == "respond":
            command.add_argument("--file", type=Path, required=True, help="Completed deliberation JSON.")
        if action == "submit":
            command.add_argument("--file", type=Path, required=True, help="The board's filled-in JSON entry.")
        if action == "progress":
            command.add_argument("--json", action="store_true", help="Print the progress as JSON.")
        if action == "authorize":
            command.add_argument("--action", dest="external_action", required=True, choices=ACTIONS,
                                 help="Kind of external action.")
            command.add_argument("--target", required=True, help="Host or tool name, without query or private details.")
            outcome = command.add_mutually_exclusive_group(required=True)
            outcome.add_argument("--allow", action="store_true", help="Allow the action (needs a terminal).")
            outcome.add_argument("--deny", action="store_true", help="Refuse the action.")
            command.add_argument("--by", required=True, help="Decision owner's name.")
            command.add_argument("--reason", required=True, help="Reason for the decision.")
        if action == "decide":
            outcome = command.add_mutually_exclusive_group(required=True)
            outcome.add_argument("--approve", action="store_true", help="Approve the reviewed scope in a terminal.")
            outcome.add_argument("--request-changes", action="store_true", help="Send the task back to consultation.")
            command.add_argument("--by", required=True, help="Decision owner's name.")
            command.add_argument("--reason", required=True, help="Reason for the decision.")
            command.add_argument("--signature", type=Path, help="SSH signature of task decision-payload (signed projects).")
            command.add_argument("--signer", help="Principal in the allowed signers file (signed projects).")
        if action == "decision-payload":
            command.add_argument("--by", required=True, help="Decision owner's name.")
            command.add_argument("--reason", required=True, help="Reason for the decision.")
            command.add_argument("--output", type=Path, help="Write the exact bytes to sign to this file.")
    return parser


def _print_json(data) -> None:
    print(json_text(data), end="")


def _installed_catalog(args, state: Path) -> tuple[dict, dict[str, set[str]]]:
    """The installed catalog with project additions, and the IDs that ship with OpenGuildKit."""
    if (args.policy_repo is None) != (args.policy_commit is None):
        raise ValueError("Supply --policy-repo and --policy-commit together.")
    policy = None
    if args.policy_repo is not None:
        policy = (args.policy_commit, read_policy_pack(args.policy_repo, args.policy_commit))
    live = load_catalog(state / "catalog", central_policy=policy)
    bundled = {collection: set(entries) for collection, entries in load_catalog().items()}
    return live, bundled


def _load_project_catalog(args, state: Path) -> dict:
    """The catalog in force for the project: locked records take precedence over installed ones."""
    live, _ = _installed_catalog(args, state)
    return apply_lock(live, read_lock(state))


def _stale_tasks(state: Path, profile: dict, catalog: dict) -> list[dict]:
    stale = []
    directory = state / "tasks"
    if not directory.is_dir():
        return stale
    for path in sorted(directory.glob("*.json")):
        try:
            task = read_task(state, path.stem)
        except ValueError:
            continue
        if task.get("profile_fingerprint") and task["profile_fingerprint"] != profile_fingerprint(profile, catalog):
            stale.append({"id": task["id"], "status": task["status"]})
    return stale


def _update_catalog(args, root: Path, state: Path) -> None:
    with project_lock(state):
        live, bundled = _installed_catalog(args, state)
        lock = read_lock(state)
        if lock is None:
            catalog = live
            profile = load_profile(state, catalog)
            if args.apply:
                sync_instructions(root, profile, catalog)
                write_lock(state, build_lock(profile, catalog, bundled))
            result = {"changes": [], "conflicts": [], "applied": args.apply, "stale_tasks": [],
                      "note": "The project had no catalog lock. It now pins the installed catalog." if args.apply
                      else "The project has no catalog lock yet. Run openguildkit update --apply or sync to create one."}
        else:
            current = apply_lock(live, lock)
            profile = load_profile(state, current)
            target = update_target(live, lock)
            changes = catalog_changes(lock, live, target, profile, bundled)
            conflicts = [conflict for conflict in rule_conflicts(profile, target) if conflict["resolution"] is None]
            result = {"changes": changes, "conflicts": conflicts, "applied": False, "stale_tasks": []}
            if args.apply and changes:
                sync_instructions(root, profile, target)
                write_lock(state, build_lock(profile, target, bundled, lock))
                result["applied"] = True
                result["stale_tasks"] = _stale_tasks(state, profile, target)
    _print_update(result, args.json)


def _print_update(result: dict, as_json: bool) -> None:
    if as_json:
        _print_json(result)
        return
    if result.get("note"):
        print(result["note"])
        return
    if not result["changes"]:
        print("The project's rules match the installed catalog.")
        return
    for change in result["changes"]:
        label = f"{change['collection']}/{change['id']}"
        if change["status"] == "added":
            print(f"+ {label} {change.get('version') or ''}".rstrip())
            continue
        if change["status"] != "changed":
            print(f"- {label}: {change['status']}, kept from the lock until you remove it from .openguildkit/project.json")
            continue
        version = f" {change['version'][0]} -> {change['version'][1]}" if "version" in change else ""
        print(f"~ {label}{version} ({', '.join(change['fields'])})")
        for rule in change.get("rules", {}).get("added", []):
            print(f"    + rule {rule['id']}: {rule['text']}")
        for rule_id in change.get("rules", {}).get("removed", []):
            print(f"    - rule {rule_id}")
        for rule in change.get("rules", {}).get("changed", []):
            print(f"    ~ rule {rule['id']}: {rule['after']}")
        for url in change["sources"]["added"]:
            print(f"    + source {url}")
        for url in change["sources"]["removed"]:
            print(f"    - source {url}")
    for conflict in result["conflicts"]:
        print(f"! {' and '.join(conflict['packs'])} conflict: record a resolution before applying.")
    if not result["applied"]:
        print("Nothing changed. Run openguildkit update --apply to adopt these rules.")
        return
    print("Applied. The next consultations use these rules.")
    for task in result["stale_tasks"]:
        print(f"  Task {task['id']} ({task['status']}) was consulted under the previous rules: review it again.")


def _prompt_choices(question: dict, value: str):
    choices = question["choices"]
    ids = [choice["id"] for choice in choices]
    raw = [item.strip() for item in value.split(",") if item.strip()] if question.get("type") == "multi-select" else [value]
    selected = []
    for item in raw:
        if item in ids:
            selected.append(item)
        elif item.isascii() and item.isdecimal() and 1 <= int(item) <= len(ids):
            selected.append(ids[int(item) - 1])
        else:
            raise ValueError(f"Unknown choice: {item}")
    return selected if question.get("type") == "multi-select" else selected[0]


def _prompt_questions(questions: list[dict], command: str) -> dict:
    if not sys.stdin.isatty():
        raise ValueError(f"{command} needs a terminal or --answers PATH.")
    answers = {}
    for question in questions:
        print(f"\n{question['prompt']}", file=sys.stderr)
        for index, choice in enumerate(question.get("choices", []), start=1):
            print(f"  {index}. {choice['label']} [{choice['id']}]", file=sys.stderr)
        kind = question.get("type", "select")
        if kind in {"list", "multi-select"}:
            print("  Separate multiple entries with commas.", file=sys.stderr)
        if question["id"] == "packs":
            print("  Enter '-' for the required workflow pack and any selected pattern packs.", file=sys.stderr)
        if question["id"] == "patterns":
            print("  Enter '-' to skip patterns, or select all the IDs you want to apply.", file=sys.stderr)
        if "default" in question:
            default = question["default"]
            shown = ", ".join(map(str, default)) if isinstance(default, list) else default
            print(f"  Default: {shown or 'none'}", file=sys.stderr)
        while True:
            print("> ", end="", file=sys.stderr, flush=True)
            value = input().strip()
            if not value and "default" in question:
                answers[question["id"]] = question["default"]
                break
            if not value and kind in {"list", "multi-select"}:
                break
            if value == "-" and question["id"] in {"packs", "patterns"}:
                answers[question["id"]] = []
                break
            if not value:
                print("  Choose an answer.", file=sys.stderr)
                continue
            try:
                if "choices" in question:
                    answer = _prompt_choices(question, value)
                elif kind == "list":
                    answer = [item.strip() for item in value.split(",") if item.strip()]
                else:
                    answer = value
            except ValueError as error:
                print(f"  {error}", file=sys.stderr)
                continue
            answers[question["id"]] = answer
            break
    return answers


def _prompt_components(question: dict) -> list[dict]:
    """Show the detected components and let the person keep them or describe the stack instead."""
    detected = question.get("default") or []
    if not detected:
        return []
    print(f"\n{question['prompt']}", file=sys.stderr)
    for component in detected:
        print(f"  {component['id']} ({component['path']}): {', '.join(component['stack'])}", file=sys.stderr)
    print("  Keep these components? [Y/n/e] (e corrects a component's stack and versions)", file=sys.stderr)
    while True:
        print("> ", end="", file=sys.stderr, flush=True)
        value = input().strip().casefold()
        if value in ("", "y", "yes", "o", "oui"):
            return detected
        if value in ("n", "no", "non"):
            return []
        if value in ("e", "edit"):
            return _edit_components(detected)
        print("  Answer y, n or e.", file=sys.stderr)


def _edit_components(detected: list[dict]) -> list[dict]:
    """Ask for each component's stack, Enter keeping the detected one, so one wrong version costs one line."""
    edited = []
    for component in detected:
        print(f"  {component['id']}: {', '.join(component['stack'])}", file=sys.stderr)
        print("  Enter to keep, or the corrected stack separated by commas (for example PostgreSQL 16).", file=sys.stderr)
        print("> ", end="", file=sys.stderr, flush=True)
        value = input().strip()
        stack = [item.strip() for item in value.split(",") if item.strip()]
        edited.append({**component, "stack": stack or component["stack"]})
    return edited


def _scoped_patterns(selected: list, plan: dict) -> list:
    """Keep the components on which each chosen stack pattern matched."""
    scopes = {pattern["id"]: pattern.get("components") for pattern in [*plan["matched"], *plan["suggested"]]}
    return [
        {"id": pattern_id, "components": scopes[pattern_id]}
        if isinstance(pattern_id, str) and scopes.get(pattern_id) else pattern_id
        for pattern_id in selected
    ]


def _print_plan_notes(plan: dict) -> None:
    for pattern in plan["matched"]:
        if pattern.get("version_unknown"):
            print(f"  {pattern['id']}: confirm {', '.join(pattern['version_unknown'])}", file=sys.stderr)
    for item in plan.get("excluded", []):
        where = f" in {item['component']}" if item["component"] else ""
        print(f"  {item['id']} does not apply{where}: {'; '.join(item['reasons'])}", file=sys.stderr)


def _prompt_task_questions(brief: str) -> dict:
    """Ask the base questions, then the follow-ups that the first answers call for."""
    base = [question for question in task_questions(brief) if question["id"] != "context"]
    answers = _prompt_questions(base, "task consult")
    follow_ups = [question for question in task_questions(brief, answers) if question["id"] not in answers]
    answers.update(_prompt_questions(follow_ups, "task consult"))
    for point in open_points(brief, validate_answers(answers)):
        print(f"  Open point: {point}", file=sys.stderr)
    return answers


def _interactive_init(questions: list[dict], catalog: dict) -> dict:
    by_id = {question["id"]: question for question in questions}
    answers = _prompt_questions([by_id["name"], by_id["sector"]], "init")
    components = _prompt_components(by_id["components"])
    if components:
        answers["components"] = components
    else:
        answers.update(_prompt_questions([by_id["stack"]], "init"))
    answers.update(_prompt_questions([by_id["roles"]], "init"))
    asked = {"name", "sector", "stack", "components", "roles"}
    pending = [question for question in questions if question["id"] not in asked]
    plan = profile_recommendations(answers, catalog)
    _print_plan_notes(plan)
    matched = [pattern["id"] for pattern in plan["matched"]]
    suggested = [pattern["id"] for pattern in plan["suggested"]]
    if suggested:
        print(f"\nMethods and alternatives to consider: {', '.join(suggested)}", file=sys.stderr)
    for question in pending:
        if question["id"] == "patterns":
            question["default"] = matched
            position = {pattern_id: index for index, pattern_id in enumerate([*matched, *suggested])}
            question["choices"].sort(key=lambda choice: position.get(choice["id"], len(position)))
    answers.update(_prompt_questions(pending, "init"))
    if "patterns" in answers:
        answers["patterns"] = _scoped_patterns(answers["patterns"], plan)
    return answers


def _print_catalog(catalog: dict, as_json: bool) -> None:
    groups = ("packs", "boards", "sectors")
    if as_json:
        _print_json({group: catalog[group] for group in groups})
        return
    for group in groups:
        print(group.capitalize())
        for entry in catalog[group].values():
            print(f"  {entry['id']:<24} {entry['title']}")


def _print_references(args, state: Path, catalog: dict) -> None:
    families = with_pattern_references(load_references(state / "catalog" / "references"), catalog["patterns"])
    if not args.json and all(value is None for value in (args.query, args.family, args.id)):
        print(f"{sum(len(family['references']) for family in families.values())} references")
        for family in families.values():
            print(f"  {family['id']:<14} {family['title']} ({len(family['references'])})")
        return
    matches = find_references(families, family_id=args.family, query=args.query, record_id=args.id)
    if args.json:
        _print_json(matches)
    elif args.id:
        reference = matches[0]
        print(f"{reference['title']} [{reference['id']}]")
        print(f"{reference['family_title']} / {reference['category']} / {reference['kind']}")
        if reference.get("edition"):
            print(f"Edition: {reference['edition']}")
        print(f"\n{reference['summary']}\n{reference['applies_when']}\n")
        if reference.get("pattern"):
            print(f"Pattern: openguildkit patterns --id {reference['pattern']}")
        packs = [catalog["packs"][pack_id] for pack_id in reference.get("packs", []) if pack_id in catalog["packs"]]
        for pack in packs:
            print(f"Pack {pack['id']}@{pack['version']}: {', '.join(rule['id'] for rule in pack['rules'])}")
        if not packs:
            print("Status: documentary reference. No pack turns it into rules yet.")
        print()
        for source in reference["sources"]:
            print(f"- {source['title']}: {source['url']}")
    elif matches:
        for reference in matches:
            print(f"  {reference['id']:<34} {reference['title']} [{reference['family']}]")
    else:
        print("No references matched.")


def _print_patterns(args, patterns: dict) -> None:
    roles = role_choices(patterns)
    if args.role and args.role not in roles:
        raise ValueError(f"Unknown role: {args.role}. Available roles: {', '.join(roles)}.")
    matches = [
        pattern for pattern in patterns.values()
        if (not args.role or args.role in pattern["roles"]) and (not args.id or args.id == pattern["id"])
    ]
    if args.id and not matches:
        raise ValueError(f"No pattern matched {args.id!r}.")
    if args.json:
        _print_json(matches)
        return
    if args.id:
        pattern = matches[0]
        print(f"{pattern['title']} [{pattern['id']}]")
        print(pattern["description"])
        print(f"Roles: {', '.join(pattern['roles'])}")
        print(f"Packs: {', '.join(pattern['packs'])}")
        if "match" in pattern:
            requirements = [" / ".join(group) for group in pattern["match"]["stack"]]
            print(f"Stack requirements: {' + '.join(requirements)}")
            if pattern["match"].get("sectors"):
                print(f"Sectors: {', '.join(pattern['match']['sectors'])}")
        for source in pattern["sources"]:
            print(f"- {source['title']}: {source['url']}")
        return
    print(f"{len(matches)} patterns")
    for pattern in matches:
        print(f"  {pattern['id']:<33} {pattern['title']} [{', '.join(pattern['roles'])}]")


def _print_recommendations(plan: dict, as_json: bool) -> None:
    if as_json:
        _print_json(plan)
        return
    print("Matched stack patterns")
    for pattern in plan["matched"]:
        where = f" [{', '.join(pattern['components'])}]" if pattern.get("components") else ""
        print(f"  {pattern['id']}{where}: {', '.join(pattern['matched_stack'])}")
        if pattern.get("version_unknown"):
            print(f"    version unknown, confirm {', '.join(pattern['version_unknown'])}")
    if plan.get("excluded"):
        print("Not applicable because of a version")
        for item in plan["excluded"]:
            where = f" [{item['component']}]" if item["component"] else ""
            print(f"  {item['id']}{where}: {'; '.join(item['reasons'])}")
    print("Methods and alternatives to consider")
    for pattern in plan["suggested"]:
        reason = ", ".join(pattern.get("matched_roles", [])) or "choose one overlapping pattern"
        print(f"  {pattern['id']}: {reason}")
    print(f"Recommended packs: {', '.join(plan['recommended_packs'])}")
    if plan.get("review"):
        print("Selected patterns to review")
        for item in plan["review"]:
            print(f"  {item['id']}: {'; '.join(item['reasons'])}")


def _print_rule_plan(plan: dict, as_json: bool, file=sys.stdout) -> None:
    if as_json:
        print(json_text(plan), end="", file=file)
        return
    print("Rule packs, in the order AGENTS.md lists them", file=file)
    for pack in plan["packs"]:
        scope = f" [{', '.join(pack['scope'])}]" if pack["scope"] else ""
        print(f"  {pack['id']}@{pack['version']}{scope}: {'; '.join(pack['provenance'])}", file=file)
    for pack_id in plan["excluded"]:
        print(f"  {pack_id}: not applied, see the rule decisions", file=file)
    if plan["conflicts"]:
        print("Conflicting packs", file=file)
        for conflict in plan["conflicts"]:
            where = f" in {', '.join(conflict['scope'])}" if conflict["scope"] else ""
            resolution = conflict["resolution"]
            outcome = (
                f"keep {', '.join(resolution['keep'])} ({resolution['reason']})" if resolution
                else "unresolved: add a resolution to the profile"
            )
            print(f"  {' / '.join(conflict['packs'])}{where}: {outcome}", file=file)
    for title, field in (("Team conventions", "conventions"), ("Project checks", "checks")):
        if plan[field]:
            print(title, file=file)
            for item in plan[field]:
                print(f"  - {item}", file=file)


def _confirm_rule_plan(plan: dict) -> None:
    print("", file=sys.stderr)
    _print_rule_plan(plan, False, file=sys.stderr)
    print("Write this profile and these rules? [Y/n]", file=sys.stderr)
    print("> ", end="", file=sys.stderr, flush=True)
    if input().strip().casefold() not in ("", "y", "yes", "o", "oui"):
        raise ValueError("Setup cancelled. Nothing was written.")


def _print_detection(detected: dict, as_json: bool) -> None:
    if as_json:
        _print_json(detected)
        return
    if not detected["components"]:
        print("No known manifest found. Describe the stack during init.")
        return
    for component in detected["components"]:
        print(f"{component['id']} ({component['path']})")
        for item in component["evidence"]:
            version = item["version"] or "version unknown"
            note = ", inferred from a client library" if item["inferred"] else ""
            print(f"  {item['name']}: {version} [{item['evidence']}{note}]")


def _selection(args) -> dict:
    delegate = {}
    for item in args.delegate:
        board_id, separator, name = item.partition("=")
        if not separator or not board_id.strip() or not name.strip():
            raise ValueError("Use --delegate BOARD=NAME.")
        delegate[board_id.strip()] = name.strip()
    return {"add": args.add, "remove": args.remove, "reason": args.reason, "delegate": delegate}


def _guard_hook(args) -> int:
    """Run as a host hook: in enforce mode, an edit the guard cannot check is refused."""
    try:
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
    except (OSError, UnicodeError):
        raw = None

    def load():
        root = project_root(args.project)
        state = state_path(root)
        catalog = _load_project_catalog(args, state)
        return root, state, load_profile(state, catalog), catalog

    code, out, err = run_hook(args.hook, raw, args.project, load)
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return code


def _guard(args, root: Path, state: Path) -> int:
    if args.ci:
        result = ci_guard(root, args.base, lambda base_state: _load_project_catalog(args, base_state))
        if args.json:
            _print_json(result)
        else:
            print(f"Guard mode on {args.base}: {result['mode']}. {result['changed']} changed file(s).")
            for task_id in result["decisions"]:
                print(f"  Covered by the decision of task {task_id} (.openguildkit/decisions/{task_id}.json).")
            for problem in result["problems"]:
                print(f"  {problem}")
        if result["ok"] or result["mode"] != "enforce":
            return 0
        return 1
    catalog = _load_project_catalog(args, state)
    profile = load_profile(state, catalog)
    approvals = covering_approvals(root, state, profile, catalog)
    result = {"mode": guard_mode(profile), "covered_by": [item["task"] for item in approvals], "approvals": approvals}
    if args.json:
        _print_json(result)
    elif not approvals:
        print(NO_TASK)
    else:
        print("Approved task(s) in progress:")
        for item in approvals:
            stale = f" (approved more than {STALE_DAYS} days ago: confirm it still applies)" if item["stale"] else ""
            print(f"  {item['task']}: {item['brief']}{stale}")
    return 0


def _deliverable(args, root: Path, state: Path, profile: dict, catalog: dict) -> int:
    templates = load_deliverables(catalog, state / "catalog" / "deliverables")
    action = args.deliverable_action
    if action == "list":
        for template in templates.values():
            method = f" (method {template['method']})" if template.get("method") else ""
            print(f"{template['id']:<24} {template['title']}{method} [{', '.join(template['brains'])}]")
        return 0
    if action == "utm":
        names = ("source", "medium", "campaign", "content", "term")
        print(utm_link(args.url, {name: getattr(args, name) for name in names}))
        return 0
    if action == "check":
        path = args.path if args.path.is_absolute() else root / args.path
        text = path.read_text(encoding="utf-8")
        match = re.search(r'^deliverable: "([a-z0-9-]+)"$', text, re.MULTILINE)
        if not match or match.group(1) not in templates:
            raise ValueError(f"{args.path} is not a draft written by openguildkit deliverable new.")
        problems = draft_problems(templates[match.group(1)], text)
        for problem in problems:
            print(f"- {problem}")
        if not problems:
            print("Ready for review by " + ", ".join(templates[match.group(1)]["brains"]))
        return 1 if problems else 0
    if args.id not in templates:
        raise ValueError(f"Unknown deliverable {args.id}. Run openguildkit deliverable list.")
    template = templates[args.id]
    if action == "show":
        print(f"{template['title']} [{template['id']}]\n{template['description']}\nBefore starting the tool:")
        for line in launch_notice(template):
            print(f"  - {line}")
        print("Checks:")
        for check in template["checks"]:
            print(f"  - {check['id']}: {check['verification']}")
        return 0
    task = read_task(state, args.task)
    output = args.output if args.output.is_absolute() else root / args.output
    entry = new_draft(template, task, args.tool, profile.get("tools", []), output)
    with project_lock(state):
        current = read_task(state, args.task)
        write_json(task_path(state, args.task), {**current, "deliverables": [*current.get("deliverables", []), entry]})
    print("Before starting the tool:")
    for line in launch_notice(template):
        print(f"  - {line}")
    print(f"Draft written to {args.output}")
    return 0


def _docs(args, root: Path, state: Path, profile: dict, catalog: dict) -> int:
    if args.docs_action == "export":
        tasks = [item.strip() for item in args.tasks.split(",") if item.strip()]
        sections = tuple(item.strip() for item in args.sections.split(",") if item.strip())
        output = args.output if args.output.is_absolute() else root / args.output
        result = export_docs(state, tasks, output, args.doc_version, sections, args.openapi, profile["name"],
                             project_overview(profile, catalog))
        for path in result["written"]:
            print(f"wrote {path}")
        for path in result["removed"]:
            print(f"removed {path}")
        for path in result["kept"]:
            print(f"kept {path}: not a page this export wrote inside {args.output.as_posix()}, left in place")
        return 0
    if args.share_action == "add":
        if not (args.deliverable and args.until and args.portal and args.by):
            raise ValueError("docs share add needs --deliverable, --recipient, --until, --portal and --by.")
        share = add_share(state, args.deliverable, args.recipient, args.until, args.portal, args.by)
        print(f"Share {share['id']} recorded. Invite the recipients in the portal's reader settings now, "
              f"and remove their access on {share['until']}.")
        return 0
    if args.share_action == "revoke":
        if not args.share_id or not args.by:
            raise ValueError("docs share revoke needs --id and --by.")
        revoke_share(state, args.share_id, args.by)
        print("revoked")
        return 0
    rows = share_status(state)
    if args.json:
        _print_json(rows)
    else:
        for row in rows:
            print(f"{row['id']} {row['deliverable']} until {row['until']} ({len(row['recipients'])} recipient(s)): {row['state']}")
    expired = [row for row in rows if row["state"].startswith("expired")]
    return 1 if args.share_action == "check" and expired else 0


def _read_consultation(task: dict) -> dict:
    if "consultation" not in task:
        raise ValueError("Run task consult first.")
    return task["consultation"]


def _print_brains(args, catalog: dict, families: dict) -> int:
    boards = catalog["boards"]
    if args.id and args.id not in boards:
        raise ValueError(f"Unknown brain: {args.id}. Run openguildkit brains.")
    if args.check:
        gaps = knowledge_gaps(catalog, families)
        for gap in gaps:
            print(f"- {gap}")
        print(f"{len(boards)} brains, {len(gaps)} gap(s).")
        return 1 if gaps else 0
    if args.json:
        if args.id:
            _print_json({**boards[args.id], "knowledge": brain_knowledge(args.id, catalog, families)})
        else:
            _print_json(boards)
    elif args.id:
        board = boards[args.id]
        print(f"{board['title']} [{board['id']}]")
        print(board["description"])
        if "review" in board:
            plan = board["review"]
            for specialist in plan["specialists"]:
                print(f"  {specialist['title']}: {specialist['focus']}")
                for check in specialist.get("checks", []):
                    print(f"    - {check}")
            for heading, key in (
                ("Evidence to inspect", "evidence"),
                ("Challenge questions", "challenge"),
                ("Decision criteria", "decision_criteria"),
            ):
                print(f"\n{heading}")
                for question in plan[key]:
                    print(f"  - {question}")
        knowledge = brain_knowledge(args.id, catalog, families)
        print("\nMethods")
        for item in knowledge["methods"]:
            print(f"  - {item['title']} [{item['id']}]")
        print("\nRule packs")
        for item in knowledge["packs"]:
            print(f"  - {item['title']} [{item['id']}@{item['version']}]")
        print("\nReferences and standards")
        print(f"  {reading_list_note(len(knowledge['references']))}")
        for item in knowledge["references"]:
            edition = f" ({item['edition']})" if item.get("edition") else ""
            print(f"  - {item['title']}{edition}: {item['url']}")
        used = [tool["title"] for tool in load_tools().values() if args.id in tool["brains"]]
        print(f"\nTools this brain can use ({len(used)}): {', '.join(used)}")
        print(f"  Details: openguildkit tools list --brain {args.id}")
    else:
        for board in boards.values():
            reviewers = board.get("review", {}).get("specialists", [])
            count = f" ({len(reviewers)} specialists)" if reviewers else ""
            print(f"  {board['id']:<23} {board['title']}{count}")
    return 0


def _new_brain(args, state: Path, catalog: dict) -> int:
    """Write a project brain's skeleton and say which tasks the new brain makes stale."""
    with project_lock(state):
        written = write_brain_skeleton(state / "catalog", args.new, catalog)
        updated = _load_project_catalog(args, state)
    for path in written:
        print(f"Wrote {path.as_posix()}")
    print("Replace each TODO, then run openguildkit brains --check and openguildkit sync.")
    if not (state / "project.json").is_file():
        return 0
    profile = load_profile(state, catalog)
    if profile["boards"]:
        print(f"The profile lists its boards: add {args.new} to boards in .openguildkit/project.json for it to review tasks.")
        return 0
    already = {task["id"] for task in _stale_tasks(state, profile, catalog)}
    stale = [task for task in _stale_tasks(state, profile, updated)
             if task["id"] not in already and task["status"] != "done"]
    for task in stale:
        print(f"Task {task['id']} ({task['status']}) was consulted without {args.new}: once you sync, "
              "it needs a new review before work continues.")
    if not stale:
        print(f"No task was reviewed under the previous board. New tasks will be routed to {args.new} by its triggers.")
    return 0


def _decide(args, root: Path, state: Path, task: dict, profile: dict) -> None:
    policy = approval_policy(profile)
    base = git_revision(root) if args.approve else None
    signature = None
    if args.approve and policy["mode"] == "signed":
        if not args.signature or not args.signer:
            raise ValueError(
                "This project requires a signed approval: run task decision-payload, sign it with ssh-keygen -Y sign "
                f"-n {NAMESPACE}, then pass --signature FILE --signer PRINCIPAL."
            )
        text = args.signature.read_text(encoding="utf-8")
        verify_signature(root, policy, decision_payload(task, args.by, args.reason, base), text, args.signer)
        signature = {"signer": args.signer, "text": text}
    blocking = blocking_alerts(load_register(state), task) if args.approve else []
    if blocking:
        raise ValueError("Approval is blocked:\n- " + "\n- ".join(blocking))
    removals = pending_removals(task) if args.approve else []
    result = record_decision(task, args.approve, args.by, args.reason, base, signature, removals)
    if args.approve and (signature is None or removals):
        if not sys.stdin.isatty():
            raise ValueError("Approval requires a person at an interactive terminal.")
        print("\n".join(decision_summary(task)), file=sys.stderr)
        if signature is None:
            print(
                json_text({"brief": task["brief"], "answers": task["answers"], "review": task["review"]}),
                file=sys.stderr,
                end="",
            )
            print("\n".join(verdict_lines(validate_review(task["consultation"], task["review"]))), file=sys.stderr)
            print(f"Type {args.id} to approve the reviewed scope: ", end="", file=sys.stderr, flush=True)
            if input().strip() != args.id:
                raise ValueError("Approval cancelled.")
        for board in removals:
            print(f"The agent removed the {board} board, which the data answer selected. "
                  f"Type {board} to confirm the removal: ", end="", file=sys.stderr, flush=True)
            if input().strip() != board:
                raise ValueError(
                    f"Approval cancelled: the removal of the {board} board was not confirmed. "
                    "Request changes and consult again with it."
                )
    with project_lock(state):
        if read_task(state, args.id) != task:
            raise ValueError("The task changed while you were deciding. Review it and retry.")
        if args.approve:
            catalog = _load_project_catalog(args, state)
            profile = load_profile(state, catalog)
            require_current_scope(task, profile, catalog)
            check_instructions(root, profile, catalog)
        write_json(task_path(state, args.id), result)
    print(result["status"])


def _task_command(args, root: Path, state: Path, profile: dict, catalog: dict) -> None:
    action = args.action
    if action == "new":
        if (args.brief is None) == (args.file is None):
            raise ValueError("Supply either a task brief or --file PATH.")
        brief = args.file.read_text(encoding="utf-8-sig") if args.file else args.brief
        check_instructions(root, profile, catalog)
        with project_lock(state):
            print(create_task(state, brief, args.id, args.follows))
        return
    if action == "list":
        rows = list_tasks(state, args.status, args.owner, args.since, args.until)
        if args.json:
            _print_json(rows)
        elif not rows:
            print("No task matches.")
        else:
            for row in rows:
                owner = f" by {row['owner']}" if row.get("owner") else ""
                follows = f" (follows {row['follows']})" if row.get("follows") else ""
                print(f"{row['id']} [{row['status']}{owner}] {row.get('brief', '')}{follows}")
                print(f"    next: {row['next']}")
        return

    task = read_task(state, args.id)
    if action == "show":
        _print_json(task)
    elif action == "questions":
        if args.answers is None:
            _print_json(task_questions(task["brief"]))
        else:
            answers = read_json(args.answers)
            questions = task_questions(task["brief"], answers)
            asked = {question["id"] for question in questions if question.get("essential") is not None}
            _print_json({
                "questions": questions,
                "follow_ups": sorted(asked - answers.keys()),
                "open_points": open_points(task["brief"], validate_answers(answers)),
            })
    elif action == "brief":
        print(_read_consultation(task)["markdown"])
    elif action == "template":
        _print_json(_read_consultation(task)["review_template"])
    elif action == "blockers":
        items = blocker_list(task)
        if args.json:
            _print_json(items)
        elif not items:
            print("No blockers.")
        else:
            for item in items:
                resolution = item["resolution"]
                state = f"{resolution['kind']} by {resolution['by']}" if resolution else "open"
                print(f"{item['id']} [{state}] {item['finding']}")
                if item.get("owner"):
                    print(f"    owner: {item['owner']}; clears when: {item['clears_when']}")
                if resolution and resolution["kind"] == "cleared":
                    print(f"    evidence: {resolution['evidence']}; supports: {resolution['supports']}")
                if resolution and resolution.get("recorded_from") == "no terminal":
                    print("    recorded without a terminal")
    elif action in ("clear", "waive"):
        if action == "waive":
            if not sys.stdin.isatty():
                raise ValueError("A waiver needs a person at an interactive terminal.")
            details = {"kind": "waived", "by": args.by, "reason": args.reason, "scope": args.scope, "until": args.until}
        else:
            details = {"kind": "cleared", "by": args.by, "evidence": args.evidence, "supports": args.supports}
        details["terminal"] = sys.stdin.isatty()
        result = resolve_blocker(task, args.blocker, details, profile, root)
        if action == "waive":
            print(f"Type {args.id} to waive {args.blocker} until {args.until}: ", end="", file=sys.stderr, flush=True)
            if input().strip() != args.id:
                raise ValueError("Waiver cancelled.")
        with project_lock(state):
            if read_task(state, args.id) != task:
                raise ValueError("The task changed while you were deciding. Review it and retry.")
            write_json(task_path(state, args.id), result)
        print(details["kind"])
    elif action == "checks":
        if task["status"] not in ("approved", "done"):
            raise ValueError("Checks belong to an approved task.")
        checks = expected_checks(task, profile)
        reported = {item["check"]: item for item in task.get("result", {}).get("checks", [])}
        if args.json:
            _print_json([{**check, "result": reported.get(check["id"])} for check in checks])
        else:
            for check in checks:
                outcome = reported.get(check["id"], {}).get("status", "not reported")
                label = check.get("command") or check.get("verification")
                print(f"{check['id']} [{'mandatory' if check['mandatory'] else 'report'}] {outcome}: {label}")
    elif action == "result" and args.template:
        if task["status"] != "approved":
            raise ValueError("Results belong to an approved task that is not done yet.")
        _print_json(result_template(task, profile))
        for check in expected_checks(task, profile):
            if check["kind"] == "command":
                print(f"{check['id']} is run and recorded by task verify: {check['command']}", file=sys.stderr)
    elif action in ("verify", "result", "complete"):
        if task["status"] != "approved":
            raise ValueError("Run checks for an approved task that is not done yet." if action == "verify"
                             else "Results belong to an approved task that is not done yet.")
        check_decision(task, root, profile)
        require_current_scope(task, profile, catalog)
        if action == "verify":
            revision = git_revision(root)
            payload = {"revision": revision["commit"] if revision else "working tree"}
            if args.chaosfolk:
                imported = chaosfolk_checks(root, args.chaosfolk, args.tool_version)
                payload.update(checks=imported["checks"], run={**imported["run"], "at": timestamp()})
            else:
                payload["checks"] = run_command_checks(root, state, task, profile)
        with project_lock(state):
            current_task = read_task(state, args.id)
            if current_task != task:
                raise ValueError("The task changed meanwhile. Retry.")
            if action == "verify":
                result = record_result(current_task, profile, payload, source="verify")
            elif action == "result":
                result = record_result(current_task, profile, read_json(args.file))
            else:
                blocking = blocking_alerts(load_register(state), current_task)
                if blocking:
                    raise ValueError("The task cannot be declared done:\n- " + "\n- ".join(blocking))
                result = complete_task(current_task, profile)
            write_json(task_path(state, args.id), result)
        if action == "complete":
            print("done")
        else:
            for item in result["result"]["checks"]:
                print(f"{item['check']}: {item['status']}")
            for name in missing_scenarios(result):
                print(f"chaosfolk:{name}: missing")
            problems = completion_problems(result, profile) + blocking_alerts(load_register(state), result)
            print(f"{len(problems)} point(s) before the task can be done." if problems else "Ready: run task complete.")
    elif action == "alerts":
        alerts_cli.task_command(args, state, task)
    elif action == "runs":
        comparison = compare_runs(task)
        if comparison is None:
            raise ValueError("The task has fewer than two ChaosFolk replays to compare.")
        if args.json:
            _print_json(comparison)
        else:
            for name, (before, after) in comparison["changed"].items():
                print(f"{name}: {before or 'absent'} -> {after or 'absent'}")
            print(f"{len(comparison['unchanged'])} scenario(s) unchanged")
    elif action == "export":
        path = export_decision(root, state, task, profile, args.output).resolve()
        print(path.relative_to(root).as_posix() if root in path.parents else str(path))
    elif action == "deliberate":
        _print_json(deliberation_request(task))
    elif action == "respond":
        payload = read_json(args.file)
        with project_lock(state):
            current_task = read_task(state, args.id)
            if current_task != task:
                raise ValueError("The task changed while you were deliberating. Run task deliberate again.")
            result = record_deliberation(current_task, payload)
            write_json(task_path(state, args.id), result)
        deliberation = result["deliberation"]
        print(f"{len(deliberation['agreed'])} resolved, {len(deliberation['open'])} open")
    elif action == "challenge":
        print(challenge_report(task), end="")
    elif action == "route":
        if args.answers is None and "answers" not in task:
            raise ValueError("Pass --answers FILE, or consult the task first.")
        answers = validate_answers(read_json(args.answers) if args.answers else task["answers"])
        selected = select_boards(
            resolved_profile(profile, catalog), catalog, {**task, "answers": answers}, answers, _selection(args)
        )
        if args.json:
            _print_json(selected)
        else:
            for board_id, reasons in selected["routing"].items():
                print(f"{board_id}: {'; '.join(reasons)}")
            for item in selected["adjustments"]:
                print(f"  {item['action']} {item['board']}: {item['reason']}")
            for pattern in selected["patterns"]:
                print(f"Suggested pattern {pattern['id']}: {pattern['reason']}. See openguildkit patterns --id {pattern['id']}.")
    elif action == "consult":
        answers = (
            read_json(args.answers)
            if args.answers is not None
            else _prompt_task_questions(task["brief"])
        )
        with project_lock(state):
            current_task = read_task(state, args.id)
            if current_task != task:
                raise ValueError("The task changed while you were answering. Review it and retry.")
            catalog = _load_project_catalog(args, state)
            profile = load_profile(state, catalog)
            check_instructions(root, profile, catalog)
            controls = host_controls(root, profile)
            market = None
            if args.market:
                research = "public-sources" if profile.get("research") == "public-sources" and answers.get("research") == "public" else "local-only"
                market = market_scope_entry(load_dossier(state, args.market), research)
            families = with_pattern_references(load_references(state / "catalog" / "references"), catalog["patterns"])
            active = set(resolved_profile(profile, catalog)["packs"])
            context = consultation_context(current_task, answers, profile, catalog)
            knowledge = {board_id: brief_knowledge(board_id, catalog, families, active, context)
                         for board_id in catalog["boards"]}
            registry = load_tools(state / "catalog" / "tools")
            for board_id in catalog["boards"]:
                usable = connected_tools(state, registry, board_id)
                if usable:
                    knowledge[board_id]["tools"] = usable
            result = record_consultation(alerts_cli.consultation_task(state, current_task), answers, profile, catalog,
                                         _selection(args), controls, market, storybook_scope_entry(state), knowledge,
                                         connected_tools(state, registry))
            result.pop("dependency_alerts")
            if "dependency_alerts" in current_task:
                result["dependency_alerts"] = current_task["dependency_alerts"]
            write_json(task_path(state, args.id), result)
        print(result["consultation"]["markdown"])
    elif action == "review":
        review = read_json(args.file) if args.file else None
        with project_lock(state):
            current_task = read_task(state, args.id)
            if current_task != task:
                raise ValueError("The task changed while you were reviewing. Read the consultation and retry.")
            result = record_review(current_task, review) if review is not None else assemble_review(current_task, args.summary)
            write_json(task_path(state, args.id), result)
        print(result["status"])
    elif action in ("pass", "submit"):
        entry = read_json(args.file) if action == "submit" else None
        with project_lock(state):
            current_task = read_task(state, args.id)
            if action == "pass":
                result = start_pass(current_task, args.board, args.mode or "sequential")
            else:
                result = submit_pass(current_task, entry, args.mode)
            error = result.pop("_error", None)
            write_json(task_path(state, args.id), result)
        if action == "pass":
            print(board_brief(result["consultation"], args.board))
        elif error:
            raise ValueError(f"The pass is recorded as failed: {error} Start it again with task pass.")
        else:
            print("done")
    elif action == "progress":
        if "consultation" not in task:
            raise ValueError("Run task consult first.")
        progress = pass_progress(task)
        if args.json:
            _print_json(progress)
        else:
            for item in progress:
                details = ", ".join(f"{key} {item[key]}" for key in ("mode", "attempts") if key in item)
                print(f"{item['board']}: {item['status']}{f' ({details})' if details else ''}")
                if "error" in item:
                    print(f"    {item['error']}")
    elif action == "decide":
        _decide(args, root, state, task, profile)
    elif action == "decision-payload":
        if task["status"] != "reviewed":
            raise ValueError("Record the boards' review before preparing a decision.")
        text = payload_text(decision_payload(task, args.by, args.reason, git_revision(root)))
        if args.output:
            # Exact UTF-8 bytes with LF line endings: a shell redirect could change the encoding.
            args.output.write_bytes(text.encode("utf-8"))
            print(args.output)
        else:
            print(text, end="")
    elif action == "authorize":
        if args.allow and not sys.stdin.isatty():
            raise ValueError("Allowing an external action requires a person at an interactive terminal.")
        result = record_authorization(task, args.external_action, args.target, args.allow, args.by, args.reason)
        if args.allow:
            print(f"Type {args.id} to allow {args.external_action} on {args.target}: ", end="", file=sys.stderr, flush=True)
            if input().strip() != args.id:
                raise ValueError("Authorization cancelled.")
        with project_lock(state):
            if read_task(state, args.id) != task:
                raise ValueError("The task changed while you were deciding. Review it and retry.")
            write_json(task_path(state, args.id), result)
        print(result["authorizations"][-1]["decision"])
    elif action == "handoff":
        output = handoff(task)
        check_decision(task, root, profile)
        require_current_scope(task, profile, catalog)
        check_instructions(root, profile, catalog)
        print(output)


def _run(args) -> int | None:
    if args.command == "guard" and args.hook:
        return _guard_hook(args)
    root = project_root(args.project)
    state = state_path(root)
    if args.command == "guard":
        return _guard(args, root, state)
    catalog = _load_project_catalog(args, state)
    if args.command == "references":
        _print_references(args, state, catalog)
    elif args.command == "patterns":
        _print_patterns(args, catalog["patterns"])
    elif args.command == "brains":
        if args.new:
            return _new_brain(args, state, catalog)
        families = with_pattern_references(load_references(state / "catalog" / "references"), catalog["patterns"])
        return _print_brains(args, catalog, families)
    elif args.command == "integrations":
        base = Path.home() if args.user else root
        for item in install_agent_files(base, args.host, args.force):
            print(f"{item['file']}: {item['action']}")
        print(f"openguildkit {package_version()}")
    elif args.command == "detect":
        _print_detection(detect_stack(root), args.json)
    elif args.command == "preview":
        profile = validate_profile(read_json(args.answers), catalog) if args.answers else load_profile(state, catalog)
        _print_rule_plan(rule_plan(profile, catalog), args.json)
    elif args.command == "recommend":
        if args.answers:
            plan = profile_recommendations(read_json(args.answers), catalog)
        else:
            profile = load_profile(state, catalog)
            plan = {**profile_recommendations(profile, catalog), "review": patterns_to_review(profile, catalog)}
        _print_recommendations(plan, args.json)
    elif args.command == "catalog":
        _print_catalog(catalog, args.json)
    elif args.command == "tools":
        return tools_cli.run(args, root, state, catalog, load_profile)
    elif args.command == "alerts":
        return alerts_cli.run(args, state)
    elif args.command == "init":
        detected = components_for_profile(detect_stack(root))
        questions = init_questions(catalog, root.name or "Project", detected)
        if args.questions:
            _print_json(questions)
            return
        if (state / "project.json").exists():
            raise ValueError("Project is already initialized. Edit .openguildkit/project.json and run openguildkit sync.")
        answers = read_json(args.answers) if args.answers else _interactive_init(questions, catalog)
        if not args.answers:
            _confirm_rule_plan(rule_plan(validate_profile(answers, catalog), catalog))
        with project_lock(state):
            live, bundled = _installed_catalog(args, state)
            profile = validate_profile(answers, live)
            initialize(root, state, profile, live)
            write_lock(state, build_lock(profile, live, bundled))
        print(f"Initialized {root}")
        warning = approval_warning(profile)
        if warning:
            print(warning, file=sys.stderr)
    elif args.command == "update":
        _update_catalog(args, root, state)
    elif args.command == "sync":
        with project_lock(state):
            live, bundled = _installed_catalog(args, state)
            lock = read_lock(state)
            catalog = apply_lock(live, lock)
            profile = load_profile(state, catalog)
            sync_instructions(root, profile, catalog)
            lock = build_lock(profile, catalog, bundled, lock)
            write_lock(state, lock)
        print("OpenGuildKit instructions updated.")
        for record in unshipped_records(lock, bundled):
            print(f"OpenGuildKit: {record} is no longer shipped; it is kept from the catalog lock until you remove it "
                  "from .openguildkit/project.json.", file=sys.stderr)
        for task in _stale_tasks(state, profile, catalog):
            if task["status"] in ("approved", "reviewed", "consulting"):
                print(f"  Task {task['id']} ({task['status']}) was consulted under the previous rules or boards: review it again.")
        for item in patterns_to_review(profile, catalog):
            print(f"OpenGuildKit: review pattern {item['id']}: {'; '.join(item['reasons'])}", file=sys.stderr)
    else:
        profile = load_profile(state, catalog)
        if args.command == "deliverable":
            return _deliverable(args, root, state, profile, catalog)
        if args.command == "docs":
            return _docs(args, root, state, profile, catalog)
        if args.command == "market":
            return market_cli.run(args, state, profile)
        if args.command == "storybook":
            return storybook_command(args, root, state)
        if args.command == "host":
            warning = approval_warning(profile)
            if warning:
                print(warning, file=sys.stderr)
            controls = host_controls(root, profile)
            if args.mode == "apply":
                for path in apply_host_settings(root, profile):
                    print(f"Wrote {path}", file=sys.stderr)
                controls = host_controls(root, profile)
            if args.json:
                _print_json(controls)
            else:
                for control in controls:
                    print(f"[{control['status']}] {control['title']}\n    {control['detail']}")
            return
        if args.command == "check":
            check_instructions(root, profile, catalog)
            missing = [control["title"] for control in host_controls(root, profile) if control["status"] == "missing"]
            if missing:
                raise ValueError(f"The host settings do not enforce: {'; '.join(missing)}. "
                                 "Run openguildkit host check for the cause, then openguildkit host apply.")
            warning = approval_warning(profile)
            if warning:
                print(warning, file=sys.stderr)
            _, bundled = _installed_catalog(args, state)
            if not lock_is_current(read_lock(state), profile, catalog, bundled):
                raise ValueError("The catalog lock does not match the profile. Run openguildkit sync.")
            print("OpenGuildKit instructions match the project profile.")
        elif args.command == "ci-check":
            result = check_export(profile, catalog, args.target_repo, args.commit)
            _print_json(result)
            return 0 if result["status"] == "success" else 1
        elif args.command == "task":
            _task_command(args, root, state, profile, catalog)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        # Redirected output is consumed by agents and scripts on every platform.
        for stream in (sys.stdout, sys.stderr):
            if not stream.isatty() and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(errors="backslashreplace")
    args = _parser().parse_args(argv)
    try:
        return _run(args) or 0
    except (OSError, ValueError, EOFError) as error:
        print(f"OpenGuildKit: {str(error) or 'Input ended before completion.'}", file=sys.stderr)
        return 2 if args.command == "ci-check" else 1
    except KeyboardInterrupt:
        print("\nOpenGuildKit: Cancelled.", file=sys.stderr)
        return 130
