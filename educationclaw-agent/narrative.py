"""
narrative.py — the story of a task, told in plain English (or German).

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

Every sentence the narrator says lives in STRINGS below, in English and
German. The file on disk is always the English telling (it is part of
the record, like events.jsonl); the dashboard asks for its own language
via /api/narrative/<id>?lang=de. Whatever is QUOTED from the run — the
task text, the model's thoughts, tool results — stays as it was.
"""

import json
import re
from datetime import datetime

from storage import NARRATIVES_FILE, atomic_write_text, read_events, read_tasks

# How much of a value to quote before cutting it off with "…".
MAX_THOUGHT = 200
MAX_ARG = 70
MAX_RESULT = 180

LANGUAGES = ("en", "de")  # app.py validates ?lang= against this

# The narrator's sentences. {name} placeholders are filled with str.format();
# only these templates must be brace-free — the values (tool arguments,
# results) are never parsed. Markdown markers (**bold**, the "→" arrows)
# are part of the sentence so the dashboard's tiny renderer sees the same
# structure in both languages.
STRINGS = {
    "all_empty": {
        "en": "# Narratives\n\nNo tasks have run yet.\n",
        "de": "# Geschichten\n\nEs ist noch keine Aufgabe gelaufen.\n",
    },
    "all_header": {
        "en": (
            "# Narratives — one story per task\n\n"
            "Assembled from logs/events.jsonl and tasks.json every time a "
            "task ends. Nothing here was written by the model.\n\n"
        ),
        "de": (
            "# Geschichten — eine pro Aufgabe\n\n"
            "Zusammengesetzt aus logs/events.jsonl und tasks.json, jedes Mal "
            "wenn eine Aufgabe endet. Nichts hiervon hat das Modell geschrieben.\n\n"
        ),
    },
    "title_task": {"en": "Task {label}", "de": "Aufgabe {label}"},
    "title_verification": {
        "en": "Verification {label} (checks the work of task {parent})",
        "de": "Prüfung {label} (kontrolliert die Arbeit von Aufgabe {parent})",
    },
    "title_subtask": {"en": "Subtask {label}", "de": "Teilaufgabe {label}"},
    "verification_text": {
        "en": "a fresh run re-checks the previous task's claim with its own tools",
        "de": "ein frischer Lauf prüft die Behauptung der vorigen Aufgabe mit eigenen Tools nach",
    },
    "outcome_label": {"en": "**Outcome:** {outcome}", "de": "**Ergebnis:** {outcome}"},
    "planning_note": {
        "en": (
            "Plan-first mode: this run only PLANNED — it could not use any tool "
            "except add_task. The work happens in the subtasks below."
        ),
        "de": (
            "Erst-planen-Modus: dieser Lauf hat nur GEPLANT — er durfte kein Tool "
            "außer add_task benutzen. Die Arbeit passiert in den Teilaufgaben darunter."
        ),
    },
    "queued_note": {
        "en": "Waiting in the queue — nothing has happened yet.",
        "de": "Wartet in der Warteschlange — bisher ist nichts passiert.",
    },
    "never_ran": {"en": "Never ran: {error}", "de": "Nie gelaufen: {error}"},
    "round": {"en": "Round {n}: {text}", "de": "Runde {n}: {text}"},
    "paused": {
        "en": "paused (step-by-step mode) until 'Next step' was pressed.",
        "de": "pausiert (Schritt-für-Schritt-Modus), bis 'Nächster Schritt' gedrückt wurde.",
    },
    "llm_error": {
        "en": "the model could not be reached ({error}).",
        "de": "das Modell war nicht erreichbar ({error}).",
    },
    "protocol_many": {
        "en": (
            "the model sent {count} tool calls at once — only the first one was run, "
            "it was told to resend the rest."
        ),
        "de": (
            "das Modell hat {count} Tool-Aufrufe auf einmal geschickt — nur der erste "
            "wurde ausgeführt, den Rest soll es erneut senden."
        ),
    },
    "protocol_none": {
        "en": "the model replied with text only, no tool call — sent back to retry.",
        "de": (
            "das Modell hat nur mit Text geantwortet, ohne Tool-Aufruf — "
            "zurückgeschickt für einen neuen Versuch."
        ),
    },
    "planning_incomplete": {
        "en": "the model tried to finish without planning a single subtask — sent back.",
        "de": (
            "das Modell wollte fertig sein, ohne eine einzige Teilaufgabe geplant "
            "zu haben — zurückgeschickt."
        ),
    },
    "finish": {
        "en": '{thought}called **finish** → "{summary}"',
        "de": '{thought}rief **finish** auf → "{summary}"',
    },
    "failed_line": {"en": "**failed** — {error}", "de": "**fehlgeschlagen** — {error}"},
    "stopped_line": {
        "en": "**stopped** by the user — everything still queued was cancelled too.",
        "de": "**gestoppt** per Stop-Knopf — alles, was noch wartete, wurde ebenfalls abgebrochen.",
    },
    "verification_queued": {
        "en": (
            "Self-verification is on: the orchestrator queued verification {label} "
            "to check this work."
        ),
        "de": (
            "Selbstprüfung ist eingeschaltet: der Orchestrator hat Prüfung {label} "
            "eingereiht, um diese Arbeit zu kontrollieren."
        ),
    },
    "called": {"en": "called **{tool}**({args})", "de": "rief **{tool}**({args}) auf"},
    "refused": {"en": "**refused:** {error}", "de": "**verweigert:** {error}"},
    "queued_subtask": {
        "en": 'queued subtask {label}: "{description}"',
        "de": 'Teilaufgabe {label} eingereiht: "{description}"',
    },
    "thought": {"en": 'thought "{thought}" → ', "de": 'dachte "{thought}" → '},
    "rounds_one": {"en": "{n} round", "de": "{n} Runde"},
    "rounds_many": {"en": "{n} rounds", "de": "{n} Runden"},
    "calls_one": {"en": "{n} LLM call", "de": "{n} LLM-Aufruf"},
    "calls_many": {"en": "{n} LLM calls", "de": "{n} LLM-Aufrufe"},
    "tokens": {
        "en": "{tokens_in} tokens read / {tokens_out} written",
        "de": "{tokens_in} Tokens gelesen / {tokens_out} geschrieben",
    },
    "model": {"en": "model {models}", "de": "Modell {models}"},
    "thousands": {"en": ",", "de": "."},  # 12,345 vs 12.345
    "outcome_done": {"en": 'done{stats} — "{result}"', "de": 'erledigt{stats} — "{result}"'},
    "outcome_failed": {"en": "failed{stats} — {error}", "de": "fehlgeschlagen{stats} — {error}"},
    "outcome_stopped": {"en": "stopped{stats} — {error}", "de": "gestoppt{stats} — {error}"},
    "outcome_running": {"en": "still running{stats}", "de": "läuft noch{stats}"},
    "outcome_queued": {"en": "waiting in the queue", "de": "wartet in der Warteschlange"},
}

# The orchestrator's own outcome sentences ("Stopped by user.") are written
# into tasks.json in English, next to a machine-readable error_code (see
# storage.py). Other languages translate the CODE; a task without a known
# code — an LLM error, a crash — shows its sentence as stored, because the
# message itself is the information.
OUTCOMES = {
    "max_rounds": {"de": "Nach {n} Runden aufgegeben (das Modell hat nie finish aufgerufen)."},
    "stopped": {"de": "Per Stop-Knopf gestoppt."},
    "cancelled_stop": {"de": "Durch Stop abgebrochen."},
    "restart_running": {"de": "Der Server wurde neu gestartet, während diese Aufgabe lief."},
    "restart_queued": {"de": "Durch den Server-Neustart abgebrochen."},
}


def _t(lang: str, key: str, **params) -> str:
    """One sentence of the story. An unknown language falls back to English."""
    entry = STRINGS[key]
    return (entry.get(lang) or entry["en"]).format(**params)


def _num(n: int, lang: str) -> str:
    """12345 -> '12,345' (en) / '12.345' (de). Done by hand on purpose: the
    locale module is process-wide, it would change the agent thread too."""
    return f"{n:,}".replace(",", _t(lang, "thousands"))


def _error_text(task: dict, lang: str) -> str:
    """The outcome sentence of a failed or stopped task, in the story's language."""
    translated = OUTCOMES.get(task.get("error_code"), {}).get(lang)
    if translated:
        return translated.format(n=task.get("iterations") or 0)
    return task.get("error") or ""


def narrate_task(
    task_id: int, tasks: list | None = None, events: list | None = None, lang: str = "en"
) -> str:
    """The markdown story of one task, including everything it spawned.
    Works for any task — a top-level one gives the whole family."""
    tasks = read_tasks() if tasks is None else tasks
    events = read_events() if events is None else events
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        return ""
    lines: list[str] = []
    _narrate(task, tasks, runs_by_task(events), lines, depth=0, lang=lang)
    return "\n".join(lines).rstrip() + "\n"


def narrate_all(tasks: list | None = None, events: list | None = None, lang: str = "en") -> str:
    """One story per top-level task, oldest first."""
    tasks = read_tasks() if tasks is None else tasks
    events = read_events() if events is None else events
    roots = [t for t in tasks if t.get("parent") is None]
    if not roots:
        return _t(lang, "all_empty")
    stories = [narrate_task(t["id"], tasks, events, lang) for t in roots]
    return _t(lang, "all_header") + "\n".join(stories)


def write_narratives():
    """Regenerate narratives.md — always in English, it is part of the
    record. Called by the agent whenever a task ends."""
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


def _narrate(task: dict, tasks: list, runs: dict, lines: list, depth: int, lang: str):
    label = task.get("label") or str(task["id"])
    kind = task.get("kind", "user")
    run = runs.get(task["id"], [])
    indent = "  " * depth

    # Heading: "## Task 1 — text" for top level, "### Subtask 1.1 — text" below.
    if depth == 0:
        title = _t(lang, "title_task", label=label)
    elif kind == "verification":
        title = _t(lang, "title_verification", label=label, parent=_parent_label(label))
    else:
        title = _t(lang, "title_subtask", label=label)
    text = task.get("task", "")
    if kind == "verification":
        text = _t(lang, "verification_text")
    heading = "##" if depth == 0 else "###"
    lines.append(f"{indent}{heading} {title} — {_clip(text, 160)}")
    lines.append("")
    lines.append(indent + _t(lang, "outcome_label", outcome=_outcome(task, run, lang)))
    lines.append("")

    start = next((e for e in run if e.get("type") == "task_start"), None)
    if start and start.get("planning"):
        lines.append(f"{indent}- {_t(lang, 'planning_note')}")

    if not run:
        if task.get("status") == "queued":
            lines.append(f"{indent}- {_t(lang, 'queued_note')}")
        elif task.get("error"):
            lines.append(f"{indent}- {_t(lang, 'never_ran', error=_error_text(task, lang))}")
        lines.append("")
        return

    round_no = 0
    first_in_round = True  # the first line of a round carries "Round n:"
    pending = None  # the tool_call waiting for its tool_result

    def bullet(text: str):
        nonlocal first_in_round
        if first_in_round:
            lines.append(f"{indent}- {_t(lang, 'round', n=round_no, text=text)}")
            first_in_round = False
        else:
            lines.append(f"{indent}  - {text}")

    for e in run:
        t = e.get("type")
        if t == "iteration_start":
            round_no = e.get("iteration", round_no + 1)
            first_in_round = True
        elif t == "paused":
            bullet(_t(lang, "paused"))
        elif t == "llm_call_error":
            bullet(_t(lang, "llm_error", error=_clip(e.get("error", ""), 100)))
        elif t == "protocol_error":
            if e.get("count", 0) > 1:
                bullet(_t(lang, "protocol_many", count=e["count"]))
            else:
                bullet(_t(lang, "protocol_none"))
        elif t == "planning_incomplete":
            bullet(_t(lang, "planning_incomplete"))
        elif t == "tool_call":
            pending = e
        elif t == "tool_result":
            call = pending or {}
            pending = None
            bullet(_describe_call(call, e, lang))
        elif t == "task_done":
            summary = _clip(e.get("summary", ""), 200)
            bullet(_t(lang, "finish", thought=_thought(e, lang), summary=summary))
        elif t == "task_failed":
            bullet(_t(lang, "failed_line", error=_clip(e.get("error", ""), 200)))
        elif t == "task_stopped":
            bullet(_t(lang, "stopped_line"))
        elif t == "task_queued" and e.get("kind") == "verification":
            note = _t(lang, "verification_queued", label=e.get("label", ""))
            lines.append(f"{indent}- {note}")
    lines.append("")

    # The family: subtasks and verifications, in the order they were created.
    for child in sorted((c for c in tasks if c.get("parent") == task["id"]), key=lambda c: c["id"]):
        _narrate(child, tasks, runs, lines, depth + 1, lang)


def _describe_call(call: dict, result_event: dict, lang: str) -> str:
    """'the model thought "..." → called set_screen(message="Hi", mood="happy") → Screen updated'"""
    tool = result_event.get("tool") or call.get("tool", "?")
    args = call.get("args") or {}
    result = str(result_event.get("result", "")).strip()
    call_text = _t(lang, "called", tool=tool, args=_format_args(args))
    # The two prefixes below are tool return strings (tools.py) — English data,
    # whatever language the story is told in.
    if result.startswith("TOOL ERROR"):
        error = _clip(result[len("TOOL ERROR") :].lstrip(": "), MAX_RESULT)
        outcome = _t(lang, "refused", error=error)
    elif tool == "add_task" and result.startswith("Task "):
        # "Task 1.2 added to the queue. It will run..." → the useful part.
        sub_label = result.split()[1]
        description = _clip(args.get("description", ""), 120)
        outcome = _t(lang, "queued_subtask", label=sub_label, description=description)
    else:
        outcome = _clip(result, MAX_RESULT)
    return f"{_thought(call, lang)}{call_text} → {outcome}"


def _thought(e: dict, lang: str) -> str:
    thought = _squash(e.get("thought") or "")
    return _t(lang, "thought", thought=_clip(thought, MAX_THOUGHT)) if thought else ""


def _format_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        parts.append(f'{k}="{_clip(v, MAX_ARG)}"')
    return ", ".join(parts)


def _outcome(task: dict, run: list, lang: str = "en") -> str:
    status = task.get("status")
    rounds = task.get("iterations") or 0
    calls = [e for e in run if e.get("type") == "llm_call_done"]
    tokens_in = sum(e.get("tokens_in") or 0 for e in calls)
    tokens_out = sum(e.get("tokens_out") or 0 for e in calls)
    stats = []
    # English and German share the same one/many split, so two keys per noun do.
    if rounds:
        stats.append(_t(lang, "rounds_one" if rounds == 1 else "rounds_many", n=rounds))
    duration = _duration(run)
    if duration:
        stats.append(duration)
    if calls:
        stats.append(_t(lang, "calls_one" if len(calls) == 1 else "calls_many", n=len(calls)))
    if tokens_in or tokens_out:
        stats.append(
            _t(lang, "tokens", tokens_in=_num(tokens_in, lang), tokens_out=_num(tokens_out, lang))
        )
    # Which model(s) answered — "a → b" if the pick was switched mid-run.
    models = list(dict.fromkeys(e["model"] for e in calls if e.get("model")))
    if models:
        stats.append(_t(lang, "model", models=" → ".join(models)))
    stat_text = f" ({', '.join(stats)})" if stats else ""

    if status == "done":
        result = _clip(task.get("result") or "", 240)
        return _t(lang, "outcome_done", stats=stat_text, result=result)
    if status == "failed":
        error = _clip(_error_text(task, lang), 240)
        return _t(lang, "outcome_failed", stats=stat_text, error=error)
    if status == "stopped":
        error = _clip(_error_text(task, lang), 240)
        return _t(lang, "outcome_stopped", stats=stat_text, error=error)
    if status == "running":
        return _t(lang, "outcome_running", stats=stat_text)
    return _t(lang, "outcome_queued")


def _duration(run: list) -> str:
    """From the task_start event to the event that ended the run. "45s" and
    "2m 05s" are unit symbols — the same in every language."""
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
