"""
tools.py — everything the agent can DO.

A "tool" is just a Python function plus a description that tells the
model when and how to use it. The TOOLS registry at the bottom is the
single source of truth: it feeds

  1. the LLM        (tools_documentation() is injected into the system prompt)
  2. the executor   (run_tool() looks up the function here)
  3. the dashboard  (the Tools panel renders this registry)

TO ADD A NEW TOOL: write a function, add one entry to TOOLS. That's it.
"""

import json
from datetime import datetime

from storage import (
    MEMORY_DIR,
    MESSAGE_FILE,
    WORKSPACE_DIR,
    atomic_write_text,
    log_event,
    next_number,
    read_status,
    read_tasks,
    slugify,
)
from storage import add_task as queue_task

# ---------------------------------------------------------------------------
# The tool functions. Each takes plain arguments and RETURNS A STRING —
# that string is what the model gets to read as the tool result.
# ---------------------------------------------------------------------------


def get_time() -> str:
    """The 'hello world' of tools: no arguments, no side effects."""
    return datetime.now().strftime("It is %A, %Y-%m-%d, %H:%M:%S.")


def set_screen(message: str, mood: str) -> str:
    """Write message.json in the agent_data folder.
    A separate screen program reads this file, so the path and the exact
    shape {"message": ..., "mood": ...} are a fixed contract."""
    message = str(message)
    allowed_moods = ("neutral", "happy", "sad")
    if mood not in allowed_moods:
        return f"TOOL ERROR: mood '{mood}' is not allowed. Use one of: {', '.join(allowed_moods)}."
    # The screen hardware can only display plain text (Latin-1). Emojis and
    # other fancy symbols have code points above 255 — reject them.
    if any(ord(ch) > 0xFF for ch in message):
        return (
            "TOOL ERROR: the screen can only display plain text. "
            "Remove all emojis and special symbols from the message."
        )
    atomic_write_text(MESSAGE_FILE, json.dumps({"message": message, "mood": mood}, indent=2))
    return f'Screen updated: "{message}" (mood: {mood}).'


def file_edit(action: str, path: str = "", content: str = "") -> str:
    """Read, write, list or delete files — but ONLY inside the workspace
    folder. The sandbox check below is what stops '../../secret.txt'."""
    if action not in ("read", "write", "list", "delete"):
        return f"TOOL ERROR: unknown action '{action}'. Use read, write, list or delete."
    if action == "list":
        names = sorted(
            f.relative_to(WORKSPACE_DIR).as_posix() for f in WORKSPACE_DIR.rglob("*") if f.is_file()
        )
        return "Files in workspace: " + (", ".join(names) if names else "(empty)")
    if not path:
        return f"TOOL ERROR: action '{action}' needs a 'path'."

    # Sandbox: resolve the full path and make sure it is INSIDE workspace/.
    full = (WORKSPACE_DIR / str(path)).resolve()
    if not full.is_relative_to(WORKSPACE_DIR.resolve()):
        return f"TOOL ERROR: path '{path}' escapes the workspace. Not allowed."

    if action == "read":
        if not full.is_file():
            return f"TOOL ERROR: file '{path}' does not exist."
        return full.read_text(encoding="utf-8")

    if action == "write":
        content = str(content)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to '{path}'."

    # delete
    if not full.is_file():
        return f"TOOL ERROR: file '{path}' does not exist."
    full.unlink()
    return f"Deleted '{path}'."


def add_task(description: str) -> str:
    """Let the AGENT create tasks: split big work into subtasks, or queue
    follow-up work it discovered in a tool result. Each subtask later runs
    as its own fresh agent run — so the description must carry ALL the
    context it needs (the subtask cannot see this conversation)."""
    description = str(description or "").strip()
    if not description:
        return "TOOL ERROR: the task description is empty."
    # Safety net: a confused model must not be able to flood the queue.
    waiting = sum(1 for t in read_tasks() if t["status"] == "queued")
    if waiting >= 12:
        return (
            f"TOOL ERROR: there are already {waiting} tasks waiting. "
            "Finish existing work before creating more tasks."
        )
    parent = read_status().get("task_id")  # the task that is running now
    task = queue_task(description, kind="subtask", parent=parent)
    log_event(
        "task_queued", kind="subtask", task_id=task["id"], label=task.get("label"), parent=parent
    )
    return (
        f"Task {task['label']} added to the queue. It will run as a fresh "
        "agent run after the current task finishes — it cannot see this "
        "conversation, only its own description. If the task has more "
        "deliverables, queue the next one now; otherwise call finish."
    )


def save_memory(title: str, content: str) -> str:
    """Store one memory as one small markdown file. All memory files are
    injected into every future LLM call — that is what 'remembering' is."""
    title, content = str(title), str(content)
    filename = f"{next_number(MEMORY_DIR):03d}_{slugify(title)}.md"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    (MEMORY_DIR / filename).write_text(
        f"# {title}\n\n(saved {stamp})\n\n{content}\n", encoding="utf-8"
    )
    return f"Memory saved as {filename}."


# ---------------------------------------------------------------------------
# The registry. Note: "finish" is NOT here on purpose — it doesn't do any
# work, it just tells the loop in agent.py to stop, so the loop handles it.
# ---------------------------------------------------------------------------

TOOLS = {
    "get_time": {
        "fn": get_time,
        "description": "Get the current date and time.",
        "args": {},
    },
    "set_screen": {
        "fn": set_screen,
        "description": (
            "Show a message on the external screen by writing "
            "message.json in the agent_data folder."
        ),
        "args": {
            "message": "the text to display — plain text only, NO emojis",
            "mood": "one of: neutral, happy, sad",
        },
    },
    "file_edit": {
        "fn": file_edit,
        "description": "Read, write, list or delete text files in your workspace folder.",
        "args": {
            "action": "one of: read, write, list, delete",
            "path": "relative file path inside the workspace (not needed for list)",
            "content": "the text to write (only for action=write)",
        },
    },
    "add_task": {
        "fn": add_task,
        "description": (
            "Queue ONE subtask for ONE deliverable (one file, one "
            "screen message, one memory). Call it once per "
            "deliverable — never bundle several into one. The "
            "subtask runs later as its OWN fresh agent run, so the "
            "description must contain everything it needs: what to "
            "produce, exact file names, required content. After "
            "queueing all subtasks, finish your own task."
        ),
        "args": {
            "description": "complete, self-contained description of ONE deliverable",
        },
    },
    "save_memory": {
        "fn": save_memory,
        "description": (
            "Permanently remember something. The memory will be "
            "included in all your future conversations."
        ),
        "args": {
            "title": "a short title for the memory",
            "content": "what to remember",
        },
    },
}


def tools_documentation() -> str:
    """Render the registry as markdown for the system prompt. The model
    only knows about tools because this text tells it about them."""
    lines = ["## Available tools\n"]
    for name, tool in TOOLS.items():
        lines.append(f"### {name}")
        lines.append(tool["description"])
        if tool["args"]:
            lines.append("Arguments:")
            for arg, desc in tool["args"].items():
                lines.append(f"- `{arg}`: {desc}")
        else:
            lines.append("Arguments: none.")
        example = {"tool": name, "args": {a: "..." for a in tool["args"]}}
        lines.append("Example:\n```json\n" + json.dumps(example) + "\n```\n")
    return "\n".join(lines)


def run_tool(name: str, args: dict) -> str:
    """Execute one tool call. IMPORTANT: errors are RETURNED as strings,
    never raised — the error text goes back to the model, which then gets
    a chance to read it and correct itself. Watch that happen live!"""
    if name not in TOOLS:
        return f"TOOL ERROR: there is no tool called '{name}'. Available tools: {', '.join(TOOLS)}."
    try:
        return TOOLS[name]["fn"](**args)
    except TypeError as exc:
        return f"TOOL ERROR: bad arguments for '{name}': {exc}"
    except Exception as exc:
        return f"TOOL ERROR: '{name}' crashed: {exc}"
