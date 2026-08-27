"""
narrative.py — the story of a task, told in plain English.

events.jsonl is a flat list of everything that ever happened, in order.
This module reads it back and turns it into the story of ONE task:

    - every loop round: what the model thought, which tool it picked
      with which arguments, and what came back,
    - the subtasks and verification runs the task spawned, each told
      the same way, nested underneath,
    - and the outcome: how many rounds, how long, how many tokens.

The result is plain markdown. The dashboard shows it when you click a
task, and agent_data/logs/narratives.md holds the story of every
top-level task (1, 2, 3, ...) — regenerated each time a task ends.
No LLM is involved: the story is assembled from the log, so it can
never claim something that did not happen.
"""

import json
import re
from datetime import datetime

from storage import NARRATIVES_FILE, atomic_write_text, read_events, read_tasks

# How much of a value to quote before cutting it off with "…".
MAX_THOUGHT = 200
MAX_ARG = 70
MAX_RESULT = 180


def narrate_task(task_id: int, tasks: list | None = None, events: list | None = None) -> str:
    """The markdown story of one task, including everything it spawned.
    Works for any task — a top-level one gives the whole family."""
    tasks = read_tasks() if tasks is None else tasks
    events = read_events() if events is None else events
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        return ""
    lines: list[str] = []
    _narrate(task, tasks, runs_by_task(events), lines, depth=0)
    return "\n".join(lines).rstrip() + "\n"


def narrate_all(tasks: list | None = None, events: list | None = None) -> str:
    """One story per top-level task, oldest first."""
    tasks = read_tasks() if tasks is None else tasks
    events = read_events() if events is None else events
    roots = [t for t in tasks if t.get("parent") is None]
    if not roots:
        return "# Narratives\n\nNo tasks have run yet.\n"
    stories = [narrate_task(t["id"], tasks, events) for t in roots]
    return (
        "# Narratives — one story per task\n\n"
        "Assembled from logs/events.jsonl and tasks.json every time a "
        "task ends. Nothing here was written by the model.\n\n" + "\n".join(stories)
    )


def write_narratives():
    """Regenerate narratives.md. Called by the agent whenever a task ends."""
    atomic_write_text(NARRATIVES_FILE, narrate_all())


# ---------------------------------------------------------------------------
# Grouping: which events belong to which task run?
# ---------------------------------------------------------------------------


def runs_by_task(events: list) -> dict:
    """Split the flat log into one slice per task run.

    Only one task runs at a time, so every 'task_start' opens a run and
    everything after it — until the next 'task_start' — happened inside
    that run. (A 'task_queued' from the dashboard can land in between;
    it is harmless, the story ignores it and reads children from
    tasks.json instead.)"""
    runs: dict = {}
    current = None
    for e in events:
        if e.get("type") == "task_start":
            current = e.get("task_id")
            runs[current] = []
        if current is not None:
            runs[current].append(e)
    return runs


# ---------------------------------------------------------------------------
# Telling the story of one run
# ---------------------------------------------------------------------------


def _narrate(task: dict, tasks: list, runs: dict, lines: list, depth: int):
    label = task.get("label") or str(task["id"])
    kind = task.get("kind", "user")
    run = runs.get(task["id"], [])
    indent = "  " * depth

    # Heading: "## Task 1 — text" for top level, "### Subtask 1.1 — text" below.
    if depth == 0:
        title = f"Task {label}"
    elif kind == "verification":
        title = f"Verification {label} (checks the work of task {_parent_label(label)})"
    else:
        title = f"Subtask {label}"
    text = task.get("task", "")
    if kind == "verification":
        text = "a fresh run re-checks the previous task's claim with its own tools"
    heading = "##" if depth == 0 else "###"
    lines.append(f"{indent}{heading} {title} — {_clip(text, 160)}")
    lines.append("")
    lines.append(f"{indent}**Outcome:** {_outcome(task, run)}")
    lines.append("")

    start = next((e for e in run if e.get("type") == "task_start"), None)
    if start and start.get("planning"):
        lines.append(
            f"{indent}- Plan-first mode: this run only PLANNED — it could not use "
            "any tool except add_task. The work happens in the subtasks below."
        )

    if not run:
        if task.get("status") == "queued":
            lines.append(f"{indent}- Waiting in the queue — nothing has happened yet.")
        elif task.get("error"):
            lines.append(f"{indent}- Never ran: {task['error']}")
        lines.append("")
        return

    round_no = 0
    first_in_round = True  # the first line of a round carries "Round n:"
    pending = None  # the tool_call waiting for its tool_result

    def bullet(text: str):
        nonlocal first_in_round
        if first_in_round:
            lines.append(f"{indent}- Round {round_no}: {text}")
            first_in_round = False
        else:
            lines.append(f"{indent}  - {text}")

    for e in run:
        t = e.get("type")
        if t == "iteration_start":
            round_no = e.get("iteration", round_no + 1)
            first_in_round = True
        elif t == "paused":
            bullet("paused (step-by-step mode) until 'Next step' was pressed.")
        elif t == "llm_call_error":
            bullet(f"the model could not be reached ({_clip(e.get('error', ''), 100)}).")
        elif t == "protocol_error":
            if e.get("count", 0) > 1:
                bullet(
                    f"the model sent {e['count']} tool calls at once — only the "
                    "first one was run, it was told to resend the rest."
                )
            else:
                bullet("the model replied with text only, no tool call — sent back to retry.")
        elif t == "planning_incomplete":
            bullet("the model tried to finish without planning a single subtask — sent back.")
        elif t == "tool_call":
            pending = e
        elif t == "tool_result":
            call = pending or {}
            pending = None
            bullet(_describe_call(call, e))
        elif t == "task_done":
            bullet(f'{_thought(e)}called **finish** → "{_clip(e.get("summary", ""), 200)}"')
        elif t == "task_failed":
            bullet(f"**failed** — {_clip(e.get('error', ''), 200)}")
        elif t == "task_stopped":
            bullet("**stopped** by the user — everything still queued was cancelled too.")
        elif t == "task_queued" and e.get("kind") == "verification":
            lines.append(
                f"{indent}- Self-verification is on: the orchestrator queued "
                f"verification {e.get('label', '')} to check this work."
            )
    lines.append("")

    # The family: subtasks and verifications, in the order they were created.
    for child in sorted((c for c in tasks if c.get("parent") == task["id"]), key=lambda c: c["id"]):
        _narrate(child, tasks, runs, lines, depth + 1)


def _describe_call(call: dict, result_event: dict) -> str:
    """'the model thought "..." → called set_screen(message="Hi", mood="happy") → Screen updated'"""
    tool = result_event.get("tool") or call.get("tool", "?")
    args = call.get("args") or {}
    result = str(result_event.get("result", "")).strip()
    call_text = f"called **{tool}**({_format_args(args)})"
    if result.startswith("TOOL ERROR"):
        outcome = f"**refused:** {_clip(result[len('TOOL ERROR') :].lstrip(': '), MAX_RESULT)}"
    elif tool == "add_task" and result.startswith("Task "):
        # "Task 1.2 added to the queue. It will run..." → the useful part.
        sub_label = result.split()[1]
        outcome = f'queued subtask {sub_label}: "{_clip(args.get("description", ""), 120)}"'
    else:
        outcome = _clip(result, MAX_RESULT)
    return f"{_thought(call)}{call_text} → {outcome}"


def _thought(e: dict) -> str:
    thought = _squash(e.get("thought") or "")
    return f'thought "{_clip(thought, MAX_THOUGHT)}" → ' if thought else ""


def _format_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        parts.append(f'{k}="{_clip(v, MAX_ARG)}"')
    return ", ".join(parts)


def _outcome(task: dict, run: list) -> str:
    status = task.get("status")
    rounds = task.get("iterations") or 0
    calls = [e for e in run if e.get("type") == "llm_call_done"]
    tokens_in = sum(e.get("tokens_in") or 0 for e in calls)
    tokens_out = sum(e.get("tokens_out") or 0 for e in calls)
    stats = []
    if rounds:
        stats.append(f"{rounds} round{'s' if rounds != 1 else ''}")
    duration = _duration(run)
    if duration:
        stats.append(duration)
    if calls:
        stats.append(f"{len(calls)} LLM call{'s' if len(calls) != 1 else ''}")
    if tokens_in or tokens_out:
        stats.append(f"{tokens_in:,} tokens read / {tokens_out:,} written")
    stat_text = f" ({', '.join(stats)})" if stats else ""

    if status == "done":
        return f'done{stat_text} — "{_clip(task.get("result") or "", 240)}"'
    if status == "failed":
        return f"failed{stat_text} — {_clip(task.get('error') or '', 240)}"
    if status == "stopped":
        return f"stopped{stat_text} — {_clip(task.get('error') or '', 240)}"
    if status == "running":
        return f"still running{stat_text}"
    return "waiting in the queue"


def _duration(run: list) -> str:
    """From the task_start event to the event that ended the run."""
    times = [e.get("time") for e in run if e.get("time")]
    if len(times) < 2:
        return ""
    try:
        first, last = datetime.fromisoformat(times[0]), datetime.fromisoformat(times[-1])
        seconds = int((last - first).total_seconds())
    except ValueError:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def _parent_label(label: str) -> str:
    return ".".join(label.split(".")[:-1]) or "?"


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _clip(text: str, n: int) -> str:
    text = _squash(text)
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"
