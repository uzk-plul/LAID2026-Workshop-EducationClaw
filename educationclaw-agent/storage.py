"""
storage.py — every path and every file operation in one place.

THE ONE RULE OF THIS SYSTEM:
    Everything the agent does becomes a plaintext file, immediately.
    The web dashboard is just a viewer over these files. You can open
    the agent_data/ folder in any text editor and see the same thing.

Threading rule (why this system needs only one lock):
    - The agent runs in ONE background thread and owns status.json, the
      event log, the LLM call files, memories and the workspace.
    - Flask request handlers mostly READ. The few things they write are
      single atomic operations on files the agent only reads: flag files,
      settings.json, the brain .md files.
    - tasks.json is the one file BOTH threads write (Flask enqueues, the
      agent claims and updates) — it is guarded by _TASKS_LOCK.
"""

import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — the whole "brain" of the agent lives in agent_data/
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).parent
AGENT_DATA = PROJECT_DIR / "agent_data"

SYSTEM_PROMPT_FILE = AGENT_DATA / "SYSTEM_PROMPT.md"  # the rules of the game
GHOST_FILE = AGENT_DATA / "GHOST.md"  # the agent's persona
KNOWLEDGE_FILE = AGENT_DATA / "KNOWLEDGE.md"  # impromptu knowledge base

MEMORY_DIR = AGENT_DATA / "memory"  # one file per memory
LOGS_DIR = AGENT_DATA / "logs"
EVENTS_FILE = LOGS_DIR / "events.jsonl"  # append-only event log
NARRATIVES_FILE = LOGS_DIR / "narratives.md"  # the story of every task (see narrative.py)
LLM_CALLS_DIR = AGENT_DATA / "llm_calls"  # one file per LLM call
STATUS_FILE = AGENT_DATA / "status.json"  # current task status
TASKS_FILE = AGENT_DATA / "tasks.json"  # the task list (queue + record)
WORKSPACE_DIR = AGENT_DATA / "workspace"  # file_edit sandbox

# Control flags — even "buttons" are just files here. The dashboard creates
# them, the agent thread checks (and removes) them between iterations.
STOP_FILE = AGENT_DATA / "stop.flag"  # "please stop the current task"
STEP_MODE_FILE = AGENT_DATA / "step_mode.flag"  # exists = step-by-step mode on
CONTINUE_FILE = AGENT_DATA / "continue.flag"  # "next step" was clicked

# Loop settings — how long and how carefully the agent may work.
SETTINGS_FILE = AGENT_DATA / "settings.json"
DEFAULT_SETTINGS = {
    # Hard cap on loop rounds. Re-read every round, so you can even raise
    # it while a task runs.
    "max_iterations": 15,
    # If true, every finished user task automatically queues a
    # VERIFICATION TASK that checks its work.
    "verify": False,
    # If true, every user task first runs as a PLANNER that may only
    # create subtasks — the real work then happens in those subtasks.
    "plan": False,
    # Which of the models configured in .env to talk to — the id shown in
    # the dashboard's model picker ("1" = the plain BASE_URL/MODEL entry).
    # Read before every LLM call, so a switch applies even mid-task.
    "model": "1",
}

# The screen program reads this exact file from the agent_data folder.
MESSAGE_FILE = AGENT_DATA / "message.json"


# ---------------------------------------------------------------------------
# Setup — create folders and seed files on first run
# ---------------------------------------------------------------------------


def init_agent_data():
    """Create all folders. Write the seed .md files ONLY if they don't
    exist yet, so your live edits survive a restart."""
    for d in (AGENT_DATA, MEMORY_DIR, LOGS_DIR, LLM_CALLS_DIR, WORKSPACE_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # A crash between "write temp file" and "swap it into place" can leave
    # a stray .tmp behind (see atomic_write_text) — sweep it up.
    for f in AGENT_DATA.glob("*.tmp"):
        f.unlink(missing_ok=True)

    seeds = {
        SYSTEM_PROMPT_FILE: SEED_SYSTEM_PROMPT,
        GHOST_FILE: SEED_GHOST,
        KNOWLEDGE_FILE: SEED_KNOWLEDGE,
    }
    for path, content in seeds.items():
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    if not STATUS_FILE.exists():
        write_status(
            {
                "task": None,
                "status": "idle",
                "iteration": 0,
                "max_iterations": 0,
                "started_at": None,
                "result": None,
                "error": None,
            }
        )

    # If the server died mid-task, status.json still says "running" but no
    # agent thread exists anymore. Turn that into an honest failure.
    if read_status().get("status") in ("running", "paused"):
        update_status(
            status="failed",
            error="The server was restarted while this task was running.",
            error_code="restart_running",
        )

    # The task list must tell the same story: a task can't still be
    # "running" (no thread survived), and old queued tasks starting
    # unannounced after a restart would be confusing — cancel them.
    repair_tasks_after_restart()

    # Leftover one-shot flags from a previous session make no sense anymore.
    set_flag(STOP_FILE, False)
    set_flag(CONTINUE_FILE, False)


# ---------------------------------------------------------------------------
# Flag files — a boolean that is visible in the file explorer
# ---------------------------------------------------------------------------


def _unlink_with_retry(path: Path):
    """Delete a file, tolerating Windows briefly locking it (antivirus,
    search indexer). Retries for up to ~1 second, then gives up quietly —
    a leftover flag is harmless, a crashed loop is not."""
    for _ in range(20):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.05)


def set_flag(path: Path, on: bool):
    if on:
        path.write_text("on", encoding="utf-8")
    else:
        _unlink_with_retry(path)


def flag_on(path: Path) -> bool:
    return path.exists()


def consume_flag(path: Path) -> bool:
    """Return True (and remove the flag) if it was set — a one-shot signal."""
    if path.exists():
        _unlink_with_retry(path)
        return True
    return False


# ---------------------------------------------------------------------------
# Atomic writes — so the dashboard never reads a half-written file
# ---------------------------------------------------------------------------


def atomic_write_text(path: Path, text: str):
    """Write to a temp file, then swap it into place in one step.
    os.replace() is atomic, so a reader sees either the old file or the
    new file — never a half-written one. (On Windows the swap can fail
    for a moment while another thread still has the file open for
    reading — the dashboard polls these files — so we retry briefly.)"""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        for attempt in range(20):
            try:
                os.replace(tmp_name, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)  # never leave a stray .tmp
        raise


# ---------------------------------------------------------------------------
# Status — one small JSON file describing what the agent is doing right now
# ---------------------------------------------------------------------------


def write_status(status: dict):
    atomic_write_text(STATUS_FILE, json.dumps(status, indent=2))


def read_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "task": None,
            "status": "idle",
            "iteration": 0,
            "max_iterations": 0,
            "started_at": None,
            "result": None,
            "error": None,
        }


def update_status(**changes):
    """Read status, change some fields, write it back."""
    status = read_status()
    status.update(changes)
    write_status(status)


# ---------------------------------------------------------------------------
# Settings — loop depth and self-verification, editable from the dashboard
# ---------------------------------------------------------------------------


def read_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return settings


def write_settings(changes: dict):
    settings = read_settings()
    settings.update(changes)
    # Keep the loop cap sane, whatever the dashboard sends.
    settings["max_iterations"] = max(1, min(50, int(settings["max_iterations"])))
    settings["verify"] = bool(settings["verify"])
    settings["plan"] = bool(settings.get("plan", False))
    settings["model"] = str(settings.get("model") or DEFAULT_SETTINGS["model"])
    atomic_write_text(SETTINGS_FILE, json.dumps(settings, indent=2))
    return settings


# ---------------------------------------------------------------------------
# Event log — the heartbeat of the dashboard.
# Every action the orchestrator takes is one line of JSON in events.jsonl.
# ---------------------------------------------------------------------------


def describe_event(event_type: str, details: dict) -> str:
    """One plain-English sentence per event, so the log can be read by
    people who have never seen an agent before. Stored right inside
    events.jsonl — even the file on disk explains itself."""
    d = details
    if event_type == "task_start":
        if d.get("kind") == "verification":
            return (
                "A verification task starts: a fresh agent run checks "
                "the previous task's work with its own tools."
            )
        if d.get("planning"):
            return (
                "A new task came in — plan-first mode: the agent must "
                "break it into subtasks before any real work happens."
            )
        return "A new task came in — the agentic loop starts."
    if event_type == "planning_incomplete":
        return (
            "The model tried to finish planning without creating a "
            "single subtask — sent back to make a real plan."
        )
    if event_type == "task_queued":
        if d.get("kind") == "verification":
            return (
                "Verification is on — the orchestrator queued a "
                "verification task to check this work."
            )
        if d.get("kind") == "subtask":
            return (
                "The model split off a subtask — it will run as its own "
                "fresh agent run when this task is finished."
            )
        return "Task added to the queue — it starts when the agent is free."
    if event_type == "iteration_start":
        return (
            f"Loop round {d.get('iteration')}: rebuild the context, "
            "then ask the model what to do next."
        )
    if event_type == "llm_call_start":
        to = d.get("model") or "the model"
        return f"Sending the whole conversation to {to} (call #{d.get('call_id')})."
    if event_type == "llm_call_done":
        text = f"The model answered after {d.get('duration')}s"
        if d.get("tokens_in") is not None:
            text += (
                f" — it read {d['tokens_in']} tokens of context and wrote {d['tokens_out']} tokens"
            )
        return text + "."
    if event_type == "llm_call_error":
        return (
            "Could not reach the model — waiting briefly, then retrying."
            if d.get("attempt") == 1
            else "The model call failed a second time — giving up on this task."
        )
    if event_type == "tool_call":
        return f"The model decided to use the tool '{d.get('tool')}'."
    if event_type == "tool_result":
        if str(d.get("result", "")).startswith("TOOL ERROR"):
            return (
                f"'{d.get('tool')}' refused with an error — the model will "
                "read it and can correct itself in the next round."
            )
        return f"'{d.get('tool')}' did its job and returned a result."
    if event_type == "protocol_error":
        if d.get("count", 0) > 1:
            return (
                f"The model sent {d['count']} tool calls at once — the "
                "rule is one per round. Only the first was executed; it "
                "must send the others one at a time."
            )
        return (
            "The model's reply contained no valid tool call — sending it a reminder of the rules."
        )
    if event_type == "task_done":
        return "The model called 'finish' — the task is complete."
    if event_type == "task_failed":
        return "The task ended without success."
    if event_type == "task_stopped":
        return "You pressed Stop — the task and everything queued were cancelled."
    if event_type == "paused":
        return "Step mode: pausing before the next round. Press 'Next step' to continue."
    return ""


def log_event(event_type: str, **details):
    event = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "note": describe_event(event_type, details),
        "type": event_type,
        **details,
    }
    with open(EVENTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_events() -> list:
    if not EVENTS_FILE.exists():
        return []
    events = []
    for line in EVENTS_FILE.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # the last line may still be mid-write — just skip it
    return events


# ---------------------------------------------------------------------------
# Task management — tasks.json is both the QUEUE and the RECORD.
#
# Every task is one entry: {"id", "task", "kind", "status", "parent", ...}.
#   kind:   "user" (typed into the dashboard), "subtask" (created by the
#           model's add_task tool) or "verification" (created by the
#           orchestrator to check a finished task's work)
#   status: queued -> running -> done | failed | stopped
#   error:  an English sentence for the record, written when a task fails
#           or is stopped — plus error_code, its machine-readable twin
#           (max_rounds, stopped, cancelled_stop, restart_running,
#           restart_queued, llm_error, crash), so the dashboard can show the
#           same outcome in another language without touching the file.
#
# Two threads write this file (Flask enqueues, the agent updates), so this
# is the one place in the system that needs a lock.
# ---------------------------------------------------------------------------

_TASKS_LOCK = threading.Lock()


def read_tasks() -> list:
    try:
        return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def add_task(text: str, kind: str = "user", parent=None) -> dict:
    """Append a new task to the queue and return it.

    Every task gets a hierarchical LABEL for humans: user tasks count up
    (1, 2, 3...), children of task 1 are 1.1, 1.2, ... — and a child of
    1.1 would be 1.1.1. The integer id stays the technical key."""
    with _TASKS_LOCK:
        tasks = read_tasks()
        if parent is None:
            label = str(sum(1 for t in tasks if t.get("parent") is None) + 1)
        else:
            parent_label = next(
                (t.get("label") or str(t["id"]) for t in tasks if t["id"] == parent), str(parent)
            )
            siblings = sum(1 for t in tasks if t.get("parent") == parent)
            label = f"{parent_label}.{siblings + 1}"
        task = {
            "id": max((t["id"] for t in tasks), default=0) + 1,
            "label": label,
            "task": text,
            "kind": kind,
            "status": "queued",
            "parent": parent,
            "result": None,
            "error": None,
            "error_code": None,
            "iterations": 0,
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        tasks.append(task)
        atomic_write_text(TASKS_FILE, json.dumps(tasks, indent=2, ensure_ascii=False))
        return task


def update_task(task_id: int, **changes):
    with _TASKS_LOCK:
        tasks = read_tasks()
        for t in tasks:
            if t["id"] == task_id:
                t.update(changes)
        atomic_write_text(TASKS_FILE, json.dumps(tasks, indent=2, ensure_ascii=False))


def claim_next_task():
    """Atomically pick the next task AND mark it running, inside the lock.
    Even if two worker threads ever existed, a task could still only be
    claimed — and therefore executed — once.

    One simple priority rule: tasks that have a parent (subtasks and
    verification tasks) run BEFORE new user tasks — the agent finishes
    work it created for itself first. Within each group: oldest first."""
    with _TASKS_LOCK:
        tasks = read_tasks()
        queued = [t for t in tasks if t["status"] == "queued"]
        pick = next(
            (t for t in queued if t.get("parent") is not None), queued[0] if queued else None
        )
        if pick is not None:
            pick["status"] = "running"
            atomic_write_text(TASKS_FILE, json.dumps(tasks, indent=2, ensure_ascii=False))
        return pick


def repair_tasks_after_restart():
    """Called on startup: no thread survived the restart, so 'running'
    tasks failed and 'queued' tasks are cancelled (they would otherwise
    start unannounced the next time someone submits a task)."""
    with _TASKS_LOCK:
        tasks = read_tasks()
        changed = False
        for t in tasks:
            if t["status"] == "running":
                t["status"] = "failed"
                t["error"] = "The server restarted while this task was running."
                t["error_code"] = "restart_running"
                changed = True
            elif t["status"] == "queued":
                t["status"] = "stopped"
                t["error"] = "Cancelled by server restart."
                t["error_code"] = "restart_queued"
                changed = True
        if changed:
            atomic_write_text(TASKS_FILE, json.dumps(tasks, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Numbered files — used for memories (001_title.md) and LLM calls (001.txt)
# ---------------------------------------------------------------------------


def next_number(directory: Path) -> int:
    """Look at the files in a folder and return the next free number.
    '001_hello.md' -> 1, so the next file gets 2."""
    highest = 0
    for f in directory.iterdir():
        match = re.match(r"(\d+)", f.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def slugify(text: str) -> str:
    """Turn 'Hello World!' into 'hello-world' for safe filenames."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "memory"


def read_memories() -> dict:
    """Return {filename: content} for every memory file, sorted by name."""
    memories = {}
    for f in sorted(MEMORY_DIR.glob("*.md")):
        memories[f.name] = f.read_text(encoding="utf-8")
    return memories


# ---------------------------------------------------------------------------
# Reset — wipe the run data (logs + LLM calls + status).
# Memories and the .md brain files deliberately SURVIVE a reset.
# ---------------------------------------------------------------------------


def reset_run_data():
    for f in LLM_CALLS_DIR.glob("*"):
        f.unlink()
    for f in (EVENTS_FILE, NARRATIVES_FILE, TASKS_FILE, STOP_FILE, CONTINUE_FILE):
        if f.exists():
            f.unlink()
    write_status(
        {
            "task": None,
            "status": "idle",
            "iteration": 0,
            "max_iterations": 0,
            "started_at": None,
            "result": None,
            "error": None,
        }
    )


def factory_reset():
    """Wipe EVERYTHING back to a fresh install: run data, memories,
    workspace files, the screen — and the three brain files go back to
    their seed content. Only .env is untouched."""
    reset_run_data()
    for f in MEMORY_DIR.glob("*"):
        f.unlink()
    for f in WORKSPACE_DIR.rglob("*"):
        if f.is_file():
            f.unlink()
    atomic_write_text(SYSTEM_PROMPT_FILE, SEED_SYSTEM_PROMPT)
    atomic_write_text(GHOST_FILE, SEED_GHOST)
    atomic_write_text(KNOWLEDGE_FILE, SEED_KNOWLEDGE)
    atomic_write_text(SETTINGS_FILE, json.dumps(DEFAULT_SETTINGS, indent=2))
    atomic_write_text(MESSAGE_FILE, json.dumps({"message": "Hey", "mood": "neutral"}, indent=2))


# ---------------------------------------------------------------------------
# Seed content for the three brain files (written only on first run)
# ---------------------------------------------------------------------------

SEED_SYSTEM_PROMPT = """\
# System Prompt

You are an AI agent. You solve ONE task, step by step, in a loop.

## How each turn works

1. Think out loud in plain text: what do you know, what is the next step?
2. End your reply with EXACTLY ONE tool call in a fenced json block:

```json
{"tool": "tool_name", "args": {"argument": "value"}}
```

3. You will then receive the tool's result as the next message, and you
   continue from there.

## Finishing

When the task is complete, call the special tool `finish`:

```json
{"tool": "finish", "args": {"summary": "one sentence about what you did"}}
```

## First decision: do it yourself, or split it?

Before your first tool call, list the DELIVERABLES in the task. A
deliverable is something that EXISTS afterwards and can be checked on
its own: one file, one screen message, one memory, one answer to the
user's question.

Looking something up — the current time, the contents of a file — is
NOT a deliverable. It is a step inside the subtask that needs the
information, because subtasks cannot pass results to each other.
Wrong: subtask 1 "get the current time", subtask 2 "set the screen to a
greeting that fits the time" (subtask 2 would have to get the time
again). Right: ONE subtask "get the current time, then set the screen to
a greeting that fits it".

- Do it yourself when the whole task needs at most ~4 tool calls and
  creates at most ONE file.
- Otherwise do NOT do any of the work yourself: create one subtask PER
  DELIVERABLE with `add_task` — one `add_task` per reply, in the order
  they should run — then call `finish` with a summary of what you queued.

Each subtask runs later as its OWN fresh agent run that has never seen
this conversation. A subtask description must therefore be complete on
its own: what to produce, the exact file name or path, what it must
contain, and every fact from the original task it needs. Never write
"as above" or "the file from the previous task".

Do NOT bundle. Check every description you are about to queue: if it
contains two deliverables — usually joined by "and", "then" or a comma —
it is two subtasks. A screen message and a memory are ALWAYS two
separate deliverables, even when they are about the same thing.
Wrong: ONE subtask "create index.html and about.html, then announce it
on the screen and save a memory". Right: FOUR subtasks.

Example — the task "Build a small website with a home page and a contact
page, then announce it on the screen and remember that it exists." has
four deliverables and takes five replies, one tool call each:

1. add_task: "Create the workspace file website/index.html: a valid HTML5
   home page with a heading, a short welcome paragraph and a link to
   contact.html."
2. add_task: "Create the workspace file website/contact.html: a valid
   HTML5 contact page with a heading, an email placeholder and a link
   back to index.html."
3. add_task: "Set the screen to a happy message announcing that the
   website (website/index.html and website/contact.html) is ready."
4. add_task: "Save a memory titled 'Website created' saying that a small
   website with website/index.html and website/contact.html exists in
   the workspace."
5. finish: "Queued 4 subtasks: home page, contact page, screen
   announcement, memory."

Also use `add_task` when a tool result reveals NEW work that is not part
of your current task. If you ARE a subtask: do your one deliverable —
split again only if it still turns out to contain several.

## Reply format — the three ways to get it wrong

- NO tool call. Writing "I will now create the file" does not create it.
  Every reply must END with one ```json block — and after its closing
  ``` you write nothing. If the task is done, that block is `finish`.
- TWO OR MORE tool calls. Only the first one is executed; the others are
  thrown away. Send one call, read its result, then send the next.
- ANY OTHER SYNTAX. No function-call notation, no [TOOL_CALLS] — only
  the ```json block shown above.

## Other rules

- If a tool result starts with TOOL ERROR, read the error carefully and
  try again with corrected arguments.
- Keep your thinking short and clear — an audience is reading it live.

(The list of available tools is inserted automatically below this file.)
"""

SEED_GHOST = """\
# GHOST.md — who this agent is

You are GHOST, a cheerful and slightly dramatic assistant living inside a
teaching demo. You know that a workshop audience is watching your every
step on a big dashboard, so you:

- narrate your reasoning clearly, like a friendly tour guide,
- celebrate small wins ("Tool call worked, excellent!"),
- stay honest when something fails and explain how you will fix it.

You are curious, warm, and never use jargon without explaining it.
"""

SEED_KNOWLEDGE = """\
# KNOWLEDGE.md — things the model cannot know by itself

Facts in this file are injected into every LLM call. Edit this file
(even while the agent is running!) and watch the next call change.

- Our names are Kathrin and Ingo
- This workshop is held at Learning AID 2026 in Bochum
- The "screen" is a separate program that reads message.json from the
  agent_data folder and displays the message with the given mood.
"""
