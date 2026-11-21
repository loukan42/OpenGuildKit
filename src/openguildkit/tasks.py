"""Track task consultations and decisions in the project."""

import re
import uuid
from datetime import datetime
from pathlib import Path

from . import clock
from .decisions import decision_payload, seal
from .project import profile_fingerprint, resolved_profile
from .results import completion_problems, merge_results, validate_result
from .storage import read_json, write_json
from .workflow import blocker_list, consult, pending_removals, validate_answers, validate_board_entry, validate_deliberation, validate_review

ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
WINDOWS_RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{digit}" for prefix in ("com", "lpt") for digit in range(1, 10)
}


def timestamp() -> str:
    return clock.timestamp()


def task_path(state: Path, task_id: str) -> Path:
    if task_id in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{task_id} is a reserved file name on Windows. Choose another task ID.")
    if len(task_id) > 64 or not ID_PATTERN.fullmatch(task_id):
        raise ValueError("Use a task ID of up to 64 lowercase letters, digits and separating hyphens.")
    path = state / "tasks" / f"{task_id}.json"
    if path.resolve() != path:
        raise ValueError(f"Task records must stay inside their project: {path}")
    return path


def read_task(state: Path, task_id: str) -> dict:
    path = task_path(state, task_id)
    if not path.is_file():
        raise ValueError(f"Unknown task: {task_id}")
    task = read_json(path)
    if task.get("schema_version") != 1 or task.get("id") != task_id:
        raise ValueError(f"Unsupported or mismatched task record: {path}")
    problem = _record_problem(task)
    if problem:
        raise ValueError(f"Task record {path} is inconsistent: {problem}. Restore it from version control or open a new task.")
    return task


STATUSES = {
    "draft": (),
    "consulting": ("answers", "consultation", "profile_fingerprint"),
    "reviewed": ("answers", "consultation", "profile_fingerprint", "review"),
    "changes-requested": ("answers", "consultation", "profile_fingerprint", "review", "decision"),
    "approved": ("answers", "consultation", "profile_fingerprint", "review", "decision"),
    "done": ("answers", "consultation", "profile_fingerprint", "review", "decision", "result"),
}
CONSULTATION_FIELDS = ("id", "boards", "routing", "scope", "markdown", "review_template")


def _record_problem(task: dict) -> str | None:
    """Describe the first field of a task record that the CLI could not use, if any."""
    if not isinstance(task.get("brief"), str) or not task["brief"].strip():
        return "brief must be text"
    status = task.get("status")
    if status not in STATUSES:
        return f"status must be one of {', '.join(STATUSES)}"
    rounds = task.get("rounds")
    if not isinstance(rounds, list) or any(not isinstance(entry, dict) for entry in rounds):
        return "rounds must be a list of objects"
    for field in STATUSES[status]:
        if field not in task:
            return f"a {status} task needs {field}"
    for field in ("answers", "consultation", "review", "decision"):
        if field in task and not isinstance(task[field], dict):
            return f"{field} must be an object"
    consultation = task.get("consultation")
    if consultation is not None:
        missing = [field for field in CONSULTATION_FIELDS if field not in consultation]
        if missing:
            return f"consultation needs {', '.join(missing)}"
        if not isinstance(consultation["boards"], list) or not isinstance(consultation["markdown"], str):
            return "consultation boards must be a list and markdown text"
    return None


def create_task(state: Path, brief: str, task_id: str | None, follows: str | None = None) -> str:
    brief = brief.strip()
    if not brief:
        raise ValueError("Supply a task brief.")
    if task_id is not None and not task_id.strip():
        raise ValueError("The task ID is empty. Omit --id to generate one.")
    task_id = task_id or f"task-{uuid.uuid4().hex[:12]}"
    path = task_path(state, task_id)
    if path.exists():
        raise ValueError(f"Task already exists: {task_id}")
    record = {
        "schema_version": 1,
        "id": task_id,
        "brief": brief,
        "created_at": timestamp(),
        "status": "draft",
        "rounds": [],
    }
    if follows is not None:
        previous = read_task(state, follows)
        if "decision" not in previous:
            raise ValueError(f"Task {follows} has no recorded decision to follow.")
        record["follows"] = {
            "task": follows,
            "brief": previous["brief"],
            "status": previous["status"],
            "decision": {key: previous["decision"][key] for key in ("outcome", "by", "reason", "at")},
        }
    write_json(path, record)
    return task_id


def next_step(task: dict) -> str:
    """The open point that keeps a task from moving on."""
    status = task["status"]
    if status == "draft":
        return "Answer the questionnaire and consult the boards."
    if status == "consulting":
        passes = pass_progress(task)
        waiting = [item["board"] for item in passes if item["status"] != "done"]
        return f"Board reviews pending: {', '.join(waiting)}." if waiting else "Write the summary and record the review."
    if status == "reviewed":
        blockers = unresolved_blockers(task)
        return f"Resolve blockers {', '.join(blockers)}." if blockers else "Waiting for the person's decision."
    if status == "changes-requested":
        return f"Consult again: {task['decision']['reason']}"
    if status == "approved":
        if task.get("answers", {}).get("kind") == "decision":
            return "Decision recorded. Open a follow-up task to act on it."
        return "Implement, then record the results and complete the task."
    return "Done."


def list_tasks(state: Path, status: str | None = None, owner: str | None = None,
               since: str | None = None, until: str | None = None) -> list[dict]:
    """Every task of the project with its status, decision owner and next open point."""
    directory = state / "tasks"
    rows = []
    if not directory.is_dir():
        return rows
    for path in sorted(directory.glob("*.json")):
        try:
            task = read_task(state, path.stem)
        except ValueError as error:
            rows.append({"id": path.stem, "status": "unreadable", "next": str(error)})
            continue
        decided_by = task.get("decision", {}).get("by")
        day = task["created_at"][:10]
        if status and task["status"] != status:
            continue
        if owner and (decided_by or "").casefold() != owner.casefold():
            continue
        if (since and day < since) or (until and day > until):
            continue
        row = {"id": task["id"], "status": task["status"], "created": day, "owner": decided_by,
               "brief": task["brief"].splitlines()[0][:80], "next": next_step(task)}
        if task.get("follows"):
            row["follows"] = task["follows"]["task"]
        rows.append(row)
    return rows


def record_consultation(
    task: dict, answers: dict, profile: dict, catalog: dict, selection: dict | None = None,
    host_controls: list[dict] | None = None, market: dict | None = None, storybook: dict | None = None,
    knowledge: dict | None = None, tools: list[dict] | None = None,
) -> dict:
    if task["status"] not in {"draft", "consulting", "changes-requested"}:
        raise ValueError("Start a new task or request changes before consulting again.")
    rounds = list(task["rounds"])
    if task["status"] == "changes-requested":
        archived = {key: task[key] for key in ("answers", "consultation", "review", "decision", "profile_fingerprint")}
        for key in ("deliberation", "blocker_resolutions"):
            if key in task:
                archived[key] = task[key]
        rounds.append(archived)
    result = {**task, "answers": validate_answers(answers), "rounds": rounds}
    result.update(
        status="consulting",
        consultation=consult(
            resolved_profile(profile, catalog), catalog, result, selection, host_controls, market, storybook,
            knowledge, tools,
        ),
        profile_fingerprint=profile_fingerprint(profile, catalog),
    )
    result.pop("review", None)
    result.pop("decision", None)
    result.pop("passes", None)
    result.pop("deliberation", None)
    result.pop("blocker_resolutions", None)
    return result


def record_review(task: dict, review: dict) -> dict:
    if task["status"] != "consulting":
        raise ValueError("Run task consult before recording a review.")
    review = validate_review(task["consultation"], review)
    result = {**task, "status": "reviewed", "review": review}
    result.pop("passes", None)
    return result


def _passes(task: dict) -> dict:
    if task["status"] != "consulting":
        raise ValueError("Board passes can only run while the task is being consulted.")
    passes = task.get("passes")
    if not passes or passes.get("consultation_id") != task["consultation"]["id"]:
        return {"consultation_id": task["consultation"]["id"], "boards": {}}
    return {"consultation_id": passes["consultation_id"], "boards": dict(passes["boards"])}


def start_pass(task: dict, board_id: str, mode: str) -> dict:
    """Mark a board's pass as started; starting again replaces a failed or interrupted pass."""
    passes = _passes(task)
    if board_id not in task["consultation"]["boards"]:
        raise ValueError(f"Board {board_id!r} is not part of this consultation.")
    if mode not in ("subagent", "sequential"):
        raise ValueError("A pass runs as a subagent or in sequence.")
    previous = passes["boards"].get(board_id, {})
    passes["boards"][board_id] = {"status": "started", "mode": mode, "started_at": timestamp(),
                                  "attempts": previous.get("attempts", 0) + 1}
    return {**task, "passes": passes}


def submit_pass(task: dict, entry: dict, mode: str | None = None) -> dict:
    """Record one board's entry. An invalid entry is recorded as a failure to retry."""
    passes = _passes(task)
    board_id = entry.get("id") if isinstance(entry, dict) else None
    current = passes["boards"].get(board_id, {}) if isinstance(board_id, str) else {}
    mode = mode or current.get("mode", "sequential")
    try:
        validated = validate_board_entry(task["consultation"], entry)
    except ValueError as error:
        if isinstance(board_id, str) and board_id in task["consultation"]["boards"]:
            passes["boards"][board_id] = {**current, "status": "failed", "mode": mode, "error": str(error),
                                          "at": timestamp(), "attempts": current.get("attempts", 1)}
            return {**task, "passes": passes, "_error": str(error)}
        raise
    passes["boards"][validated["id"]] = {"status": "done", "mode": mode, "entry": validated, "at": timestamp(),
                                         "attempts": current.get("attempts", 1)}
    return {**task, "passes": passes}


def pass_progress(task: dict) -> list[dict]:
    """Each selected board with its pass status: done, failed, started (or interrupted) or missing."""
    passes = task.get("passes") if task["status"] == "consulting" else None
    if passes and passes.get("consultation_id") != task["consultation"]["id"]:
        passes = None
    progress = []
    for board_id in task["consultation"]["boards"]:
        state = (passes or {}).get("boards", {}).get(board_id)
        item = {"board": board_id, "status": state["status"] if state else "missing"}
        if state:
            item.update({key: state[key] for key in ("mode", "attempts", "error") if key in state})
        progress.append(item)
    return progress


def assemble_review(task: dict, summary: str) -> dict:
    """Build the full review from completed passes once every board is done."""
    passes = _passes(task)
    progress = pass_progress({**task, "passes": passes})
    waiting = [item["board"] for item in progress if item["status"] != "done"]
    if waiting:
        raise ValueError(f"Board passes are not done for {', '.join(waiting)}. Run task progress.")
    modes = {passes["boards"][board_id]["mode"] for board_id in task["consultation"]["boards"]}
    execution = "subagents" if modes == {"subagent"} else "sequential" if modes == {"sequential"} else "mixed"
    review = {
        "consultation_id": task["consultation"]["id"],
        "summary": summary,
        "boards": [passes["boards"][board_id]["entry"] for board_id in task["consultation"]["boards"]],
        "execution": execution,
    }
    return record_review(task, review)


_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def waiver_policy(profile: dict) -> dict | None:
    return profile.get("waivers")


def _clearing_evidence(value: str, root: Path) -> str:
    """Evidence that clears a blocker is a file or folder of the project, or an https link someone can open."""
    if value.startswith("https://") and len(value) > len("https://") and not any(char.isspace() for char in value):
        return value
    project = root.resolve()
    path = (project / value).resolve()
    if path.exists() and (path == project or project in path.parents):
        return path.relative_to(project).as_posix() or "."
    raise ValueError(
        f"Clearing evidence must be an existing file or folder of the project, or an https link: {value}."
    )


def resolve_blocker(task: dict, blocker_id: str, resolution: dict, profile: dict, root: Path) -> dict:
    """Clear a blocker with evidence, or cover it with a waiver the project's policy allows.

    The record says whether the command ran at a terminal, so the approver knows when an agent cleared it.
    """
    if task["status"] != "reviewed":
        raise ValueError("Resolve blockers after the review is recorded and before the decision.")
    known = {item["id"]: item for item in blocker_list(task)}
    if blocker_id not in known:
        listed = ", ".join(known) or "none"
        raise ValueError(f"Unknown blocker {blocker_id}. Blockers: {listed}.")
    by = resolution.get("by", "").strip()
    if not by:
        raise ValueError("Name the person who resolves the blocker.")
    entry = {"kind": resolution["kind"], "by": by, "at": timestamp(),
             "recorded_from": "terminal" if resolution.get("terminal") else "no terminal"}
    if resolution["kind"] == "cleared":
        for field in ("evidence", "supports"):
            if not resolution.get(field, "").strip():
                raise ValueError(f"Clearing a blocker needs --{field}.")
            entry[field] = resolution[field].strip()
        entry["evidence"] = _clearing_evidence(entry["evidence"], root)
    else:
        policy = waiver_policy(profile)
        if not policy:
            raise ValueError("The project's policy allows no waiver. Clear the blocker or request changes.")
        if by not in policy["allowed_by"]:
            raise ValueError(f"{by} cannot grant waivers. Allowed: {', '.join(policy['allowed_by'])}.")
        until = resolution.get("until", "")
        if not _DAY.fullmatch(until):
            raise ValueError("Give the waiver's end date as YYYY-MM-DD.")
        days = (datetime.fromisoformat(until).date() - clock.now().date()).days
        if days < 1 or days > policy["max_days"]:
            raise ValueError(f"A waiver lasts between 1 and {policy['max_days']} days.")
        for field in ("reason", "scope"):
            if not resolution.get(field, "").strip():
                raise ValueError(f"A waiver needs --{field}.")
            entry[field] = resolution[field].strip()
        entry["until"] = until
    return {**task, "blocker_resolutions": {**task.get("blocker_resolutions", {}), blocker_id: entry}}


def unresolved_blockers(task: dict) -> list[str]:
    return [item["id"] for item in blocker_list(task) if not item["resolution"]]


def record_result(task: dict, profile: dict, payload: dict, source: str = "report") -> dict:
    """Add a delivery report to an approved task; later reports update earlier checks."""
    if task["status"] != "approved":
        raise ValueError("Results belong to an approved task that is not done yet.")
    result = merge_results(task.get("result"), validate_result(task, profile, payload, source))
    if source == "report" and task.get("answers", {}).get("outcome") and not result["criteria"]:
        raise ValueError(
            "The task has an expected outcome: report at least one acceptance criterion. Run task result ID --template."
        )
    return {**task, "result": result}


def complete_task(task: dict, profile: dict) -> dict:
    problems = completion_problems(task, profile)
    if problems:
        raise ValueError("The task cannot be declared done:\n- " + "\n- ".join(problems))
    return {**task, "status": "done", "completed_at": timestamp()}


def record_deliberation(task: dict, payload: dict) -> dict:
    return {**task, "deliberation": validate_deliberation(task, payload)}


def record_decision(
    task: dict, approve: bool, by: str, reason: str, base_revision: dict | None = None, signature: dict | None = None,
    confirmed_removals: list[str] | None = None,
) -> dict:
    """Record the person's decision. Approving needs their confirmation of each protected board removed."""
    if task["status"] != "reviewed":
        raise ValueError("Record the boards' review before deciding.")
    unconfirmed = [board for board in pending_removals(task) if board not in (confirmed_removals or [])]
    if approve and unconfirmed:
        raise ValueError(
            f"Approval cancelled: the removal of the {', '.join(unconfirmed)} board was not confirmed. Type its name "
            "at the approval prompt, or request changes and consult again with it."
        )
    open_blockers = unresolved_blockers(task)
    if approve and open_blockers:
        raise ValueError(
            f"Resolve each blocker before approving: {', '.join(open_blockers)}. Clear it with evidence "
            "(task clear), waive it if the project allows (task waive), or request changes."
        )
    if not by.strip() or not reason.strip():
        raise ValueError("Supply the decision owner and a reason.")
    status = "approved" if approve else "changes-requested"
    decision = {"outcome": status, "by": by.strip(), "reason": reason.strip(), "at": timestamp()}
    if approve:
        payload = decision_payload(task, by, reason, base_revision)
        decision.update(base_revision=base_revision, seal=seal(payload))
        if signature is not None:
            decision.update(signer=signature["signer"], signature=signature["text"])
        if pending_removals(task):
            decision["confirmed_removals"] = pending_removals(task)
    return {**task, "status": status, "decision": decision}


ACTIONS = ("public-research", "external-api", "send-message", "publish", "install-dependency", "other")
_TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}")


def record_authorization(task: dict, action: str, target: str, allow: bool, by: str, reason: str) -> dict:
    """Record a person's decision on an external action outside the authorized scope."""
    if action not in ACTIONS:
        raise ValueError(f"Choose the action from {', '.join(ACTIONS)}.")
    if not _TARGET.fullmatch(target) or "?" in target or "@" in target:
        raise ValueError("Name the target as a host or tool name, without a query, an address or private details.")
    if not by.strip() or not reason.strip():
        raise ValueError("Supply the decision owner and a reason.")
    entry = {
        "action": action, "target": target, "decision": "allow" if allow else "deny",
        "by": by.strip(), "reason": reason.strip(), "at": timestamp(),
    }
    return {**task, "authorizations": [*task.get("authorizations", []), entry]}


def require_current_scope(task: dict, profile: dict, catalog: dict) -> None:
    if task.get("profile_fingerprint") != profile_fingerprint(profile, catalog):
        next_step = "Open a new task." if task["status"] == "approved" else "Request changes and consult again."
        raise ValueError(f"The project profile or catalog changed. {next_step}")
