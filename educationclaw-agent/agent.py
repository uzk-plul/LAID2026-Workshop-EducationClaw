"""
agent.py — the agentic loop. This is the heart of the whole system.

An "agent" is surprisingly simple. It is a while-loop around an LLM:

    while not done:
        1. build the context (system prompt + conversation so far)
        2. ask the LLM: "what next?"
        3. the LLM answers with a tool call
        4. run that tool, append the result to the conversation
        5. repeat — until the LLM calls the special tool "finish"

Everything else (memory, knowledge, persona) is just text that gets
pasted into step 1. That's the whole trick.

TASK MANAGEMENT: tasks line up in a queue (tasks.json). One worker
thread (run_queue) takes them in order. If self-verification is on,
a finished task automatically queues a VERIFICATION TASK — a fresh
agent run whose job is to check the previous task's work.
"""

import json
import re
import threading
import time
from datetime import datetime

from llm import LLMError, call_llm
from narrative import write_narratives
from storage import (
    CONTINUE_FILE,
    GHOST_FILE,
    KNOWLEDGE_FILE,
    STEP_MODE_FILE,
    STOP_FILE,
    SYSTEM_PROMPT_FILE,
    add_task,
    claim_next_task,
    consume_flag,
    flag_on,
    log_event,
    read_memories,
    read_settings,
    read_status,
    read_tasks,
    update_status,
    update_task,
    write_status,
)
from tools import run_tool, tools_documentation

# The task text for an automatic verification run. It is a REAL task like
# any other: fresh conversation, own loop rounds, own entry in the task
# list. The verifier cannot rely on the previous run's memory of what it
# did — it has to check the actual files and results with its tools.
VERIFICATION_TASK = """\
VERIFICATION TASK — check the work of task {label}.

The original task was:
"{task}"

The agent finished it and claimed:
"{result}"

Your job: verify that claim like a skeptical reviewer.
1. List each requirement of the original task.
2. Check every one USING YOUR TOOLS (read the files, check the screen)
   — do not trust the claim.
3. If something is wrong or missing, fix it.
4. Then call finish. Start your summary with "VERIFIED:" if everything
   was correct, or "FIXED:" if you had to repair something."""

# In plan-first mode the user task is wrapped in this: the model becomes a
# pure PLANNER. The orchestrator enforces it — every tool except add_task
# is refused during planning, so the real work MUST happen in subtasks.
PLANNING_TASK = """\
PLANNING MODE — do not do the work yet.

Your only job right now is to turn this task into a plan:

"{task}"

Rules:
1. Create one subtask PER DELIVERABLE with add_task (one file, one screen
   message, one memory, one answer = one subtask), in the order they
   should run. Never bundle two deliverables into one subtask — a
   description joined by "and" or "then" is two subtasks.
2. Looking something up (the current time, a file's contents) is NOT a
   deliverable — subtasks cannot pass results to each other, so the
   subtask that needs the information must gather it itself. A task with
   one deliverable is ONE subtask, even if it takes several steps.
3. Each description must be complete and self-contained — the subtask
   runs as a fresh agent that has not seen this task: say what to
   produce, the exact file name or path, what it must contain, and every
   fact it needs.
4. In planning mode only add_task and finish work; every other tool
   will refuse.
5. When the plan is complete, call finish with a one-line overview.
   You must create at least one subtask."""


def build_system_prompt() -> str:
    """Assemble the system prompt from the plaintext brain files.

    This is re-read from disk on EVERY iteration — so if you edit
    KNOWLEDGE.md while the agent is running, the very next LLM call
    already contains your change. Try it during the workshop!
    """
    parts = [
        SYSTEM_PROMPT_FILE.read_text(encoding="utf-8"),
        tools_documentation(),
        GHOST_FILE.read_text(encoding="utf-8"),
        KNOWLEDGE_FILE.read_text(encoding="utf-8"),
    ]
    memories = read_memories()
    if memories:
        memory_text = "## Your memories\n\n" + "\n\n".join(
            f"[{name}]\n{content}" for name, content in memories.items()
        )
        parts.append(memory_text)
    return "\n\n---\n\n".join(parts)


def parse_tool_calls(reply: str) -> list:
    """Find the tool call(s) in the model's reply.

    The model was told to end its reply with EXACTLY ONE ```json fenced
    block like {"tool": "get_time", "args": {}}. We return every call we
    find, in order — the loop executes the first and tells the model
    about any others. Models are sloppy in predictable ways, so the parser
    forgives what it safely can: a missing or bare ``` fence, raw newlines
    inside strings, a '})' where '}}' was meant, and a model slipping into
    its native [TOOL_CALLS] syntax. Anything else is a protocol error.
    """
    fences = re.findall(r"```json\s*(.*?)```", reply, re.DOTALL | re.IGNORECASE)
    calls = []
    for block in fences:
        found = [o for o in _json_objects(block) if _is_call(o)]
        calls.extend(found or [o for o in _repair_json(block) if _is_call(o)])
    if not calls:
        # No usable fence — scan the whole reply for JSON objects instead.
        calls = [o for o in _json_objects(reply) if _is_call(o)]
    if not calls:
        # Mistral-style native syntax:  [TOOL_CALLS]file_edit{"action": ...}
        for name, block in re.findall(r"\[TOOL_CALLS\]\s*(\w+)\s*(\{.*)", reply, re.DOTALL):
            args = _json_objects(block)
            if args and isinstance(args[0], dict):
                calls.append({"tool": name, "args": args[0]})
    return calls


def _is_call(obj) -> bool:
    return isinstance(obj, dict) and "tool" in obj


def thought_of(reply: str) -> str:
    """The model's 'thinking out loud' — the prose BEFORE its tool call.
    Logged with every tool call so the story of the run (narrative.py)
    can quote why the model did what it did."""
    text = re.split(r"```|\[TOOL_CALLS\]", reply, maxsplit=1)[0]
    if "```" not in reply and "{" in text:
        text = text[: text.find("{")]  # a bare JSON call without a fence
    return " ".join(text.split())[:300]


_DECODER = json.JSONDecoder(strict=False)  # strict=False: allow raw newlines in strings


def _json_objects(text: str) -> list:
    """Every JSON object embedded in a text, in order. Prose around and
    between them is skipped — a '{' that does not start valid JSON is
    simply not a tool call."""
    found, i = [], 0
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = _DECODER.raw_decode(text, i)
            found.append(obj)
            i = end
        except json.JSONDecodeError:
            i += 1
    return found


def _repair_json(block: str) -> list:
    """A fenced block that does not parse — try the most common slip: the
    model closed the call with ')' instead of '}' (or forgot a brace)."""
    text = block.strip().rstrip(")")
    for extra in ("", "}", "}}"):
        try:
            return [_DECODER.decode(text + extra)]
        except json.JSONDecodeError:
            pass
    return []


# Appended to every message the orchestrator sends. The last thing the model
# reads is the format rule — that is where a reminder works best.
REPLY_REMINDER = (
    "(Reply with a short thought, then EXACTLY ONE ```json tool "
    "call. Write nothing after its closing ```.)"
)


def user_message(text: str) -> dict:
    return {"role": "user", "content": f"{text}\n\n{REPLY_REMINDER}"}


def wait_if_step_mode(iteration: int) -> bool:
    """Step mode: pause before each loop round until 'Next step' is pressed.
    The pause is just a wait-for-a-flag-file loop — the dashboard button
    creates continue.flag, we consume it. Returns False if Stop was
    pressed while waiting."""
    if not flag_on(STEP_MODE_FILE):
        return True
    update_status(status="paused")
    log_event("paused", iteration=iteration)
    consume_flag(CONTINUE_FILE)  # ignore stale clicks from before the pause
    while flag_on(STEP_MODE_FILE) and not consume_flag(CONTINUE_FILE):
        if flag_on(STOP_FILE):
            return False
        time.sleep(0.2)
    update_status(status="running")
    return True


def finish_task(task, status: str, **fields):
    """Record the outcome in BOTH places: status.json (the live spotlight)
    and the task's own entry in tasks.json (the permanent record)."""
    update_status(status=status, **fields)
    s = read_status()
    update_task(
        task["id"],
        status=status,
        iterations=s.get("iteration"),
        result=s.get("result"),
        error=s.get("error"),
    )
    # A task ended — retell the story of every top-level task in
    # logs/narratives.md (this task's own family included).
    write_narratives()


def run_queue():
    """Take queued tasks in order until the queue is empty.
    claim_next_task() marks the task running atomically, so a task can
    never be picked up twice."""
    while True:
        task = claim_next_task()
        if task is None:
            return
        outcome = run_one(task)
        if outcome == "stopped":
            # Stop means stop EVERYTHING: cancel whatever is still queued.
            while (leftover := claim_next_task()) is not None:
                update_task(leftover["id"], status="stopped", error="Cancelled by Stop.")
            write_narratives()  # the cancelled tasks are part of the story too
            consume_flag(STOP_FILE)  # the flag did its job — clear it
            return


# ---------------------------------------------------------------------------
# The worker thread. app.py calls ensure_worker() after every task
# submission; the thread drains the queue and then retires. The lock closes
# a race: a task submitted at the exact moment the worker finds the queue
# empty must not be stranded with no thread running and none starting.
# ---------------------------------------------------------------------------

_worker = None
_worker_lock = threading.Lock()


def ensure_worker():
    """Start the background worker if none is running."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_main, daemon=True)
            _worker.start()


def worker_busy() -> bool:
    return _worker is not None and _worker.is_alive()


def _worker_main():
    global _worker
    while True:
        run_queue()
        with _worker_lock:
            # Re-check under the lock: anything queued since run_queue()
            # saw an empty queue is still ours — otherwise retire.
            if not any(t["status"] == "queued" for t in read_tasks()):
                _worker = None
                return


def run_one(task) -> str:
    """Run one (already claimed) task through the agentic loop.
    Returns its final status."""
    consume_flag(STOP_FILE)  # start with a clean slate
    consume_flag(CONTINUE_FILE)
    settings = read_settings()
    # Plan-first mode: user tasks become planners (subtasks execute as-is).
    planning = settings["plan"] and task["kind"] == "user"
    write_status(
        {
            "task": task["task"],
            "task_id": task["id"],
            "label": task.get("label", str(task["id"])),
            "kind": task["kind"],
            "status": "running",
            "iteration": 0,
            "planning": planning,
            "max_iterations": settings["max_iterations"],
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "result": None,
            "error": None,
        }
    )
    log_event(
        "task_start",
        task_id=task["id"],
        label=task.get("label", str(task["id"])),
        task=task["task"][:200],
        kind=task["kind"],
        planning=planning,
    )

    # The conversation. The system prompt is NOT stored here because we
    # rebuild it fresh from the .md files on every iteration.
    first = PLANNING_TASK.format(task=task["task"]) if planning else task["task"]
    messages = [user_message(first)]
    subtasks_created = 0
    iteration = 0

    try:
        while True:
            iteration += 1
            # The round limit comes from settings.json, re-read every round
            # — so you can raise it in the dashboard while the task runs.
            max_iterations = read_settings()["max_iterations"]
            if iteration > max_iterations:
                finish_task(
                    task,
                    "failed",
                    error=f"Gave up after {max_iterations} rounds (the model never called finish).",
                )
                log_event("task_failed", error="max iterations reached")
                return "failed"

            # Both control checks live between iterations — one loop round
            # (one LLM call + one tool) always finishes as a whole.
            if not wait_if_step_mode(iteration) or consume_flag(STOP_FILE):
                finish_task(task, "stopped", error="Stopped by user.")
                log_event("task_stopped")
                return "stopped"

            update_status(iteration=iteration, max_iterations=max_iterations)
            log_event("iteration_start", iteration=iteration)

            # 1. build context  2. ask the LLM
            system = build_system_prompt()
            reply = call_llm([{"role": "system", "content": system}] + messages)
            messages.append({"role": "assistant", "content": reply})

            # 3. find the tool call in the reply — the rule is EXACTLY one
            calls = parse_tool_calls(reply)
            if not calls:
                # The model talked but did nothing. Tell it — and let it retry.
                log_event("protocol_error", iteration=iteration, count=0)
                # (Typical shape: "Let me fetch the time." — then silence.
                # Asking for the JSON block ALONE gets it back on track.)
                messages.append(
                    user_message(
                        "PROTOCOL ERROR: your reply was text only — no tool call, "
                        "so nothing happened. Reply now with ONLY the ```json "
                        'block for your next tool call, like {"tool": "...", '
                        '"args": {...}} — no prose before it.'
                    )
                )
                continue

            call = calls[0]
            name = call.get("tool")
            args = call.get("args") or {}
            warning = ""
            if len(calls) > 1:
                # Several calls at once. Later ones may depend on the first
                # one's result, so we run ONLY the first — and say so loudly
                # instead of silently dropping work.
                log_event("protocol_error", iteration=iteration, count=len(calls))
                if name == "finish":
                    messages.append(
                        user_message(
                            f"PROTOCOL ERROR: your reply contained {len(calls)} "
                            "tool calls and the first was 'finish'. Nothing was "
                            "executed. If the task is really done, send finish "
                            "alone; otherwise send the other calls one per reply."
                        )
                    )
                    continue
                warning = (
                    f"\n\nPROTOCOL ERROR: your reply contained {len(calls)} "
                    f"tool calls. Only this first one ({name}) was "
                    f"executed — the other {len(calls) - 1} were IGNORED. "
                    "Send them again, one per reply."
                )

            # "finish" is the agreed stop signal — being done is just
            # another tool call.
            if name == "finish":
                # A planner that plans nothing has not done its job.
                if planning and subtasks_created == 0:
                    log_event("planning_incomplete", iteration=iteration)
                    messages.append(
                        user_message(
                            "You are in planning mode but created no subtasks. "
                            "Use add_task now — if the task is truly one small "
                            "step, create one subtask that does exactly that."
                        )
                    )
                    continue
                summary = args.get("summary", "(no summary given)")
                log_event("task_done", summary=summary, thought=thought_of(reply))
                finish_task(task, "done", result=summary)

                # Self-verification: a finished USER task queues a
                # verification task — a separate, fresh agent run that
                # checks this work. (Verification tasks themselves are
                # never verified again, or we would loop forever.)
                if read_settings()["verify"] and task["kind"] == "user":
                    vtask = add_task(
                        VERIFICATION_TASK.format(
                            label=task.get("label", task["id"]), task=task["task"], result=summary
                        ),
                        kind="verification",
                        parent=task["id"],
                    )
                    log_event(
                        "task_queued",
                        kind="verification",
                        task_id=vtask["id"],
                        label=vtask.get("label"),
                        parent=task["id"],
                    )
                return "done"

            # 4. run the tool, append the result.
            # In planning mode the orchestrator ENFORCES the plan-only rule:
            # the model cannot sneak in real work, it can only delegate.
            log_event("tool_call", tool=name, args=args, thought=thought_of(reply))
            if planning and name != "add_task":
                result = (
                    "TOOL ERROR: planning mode — only add_task and "
                    "finish are allowed here. Create a subtask for "
                    "this work instead of doing it yourself."
                )
            else:
                result = run_tool(name, args)
                if planning and result.startswith("Task "):
                    subtasks_created += 1
            log_event("tool_result", tool=name, result=result[:500])
            messages.append(user_message(f"TOOL RESULT ({name}):\n{result}{warning}"))
            # (Real APIs have a dedicated "tool" role for this — we use a
            # plain user message so the call logs stay maximally readable.)

    except LLMError as exc:
        # If the LLM call died because the user pressed Stop, say
        # "stopped", not "failed" — the honest outcome.
        if consume_flag(STOP_FILE):
            finish_task(task, "stopped", error="Stopped by user.")
            log_event("task_stopped")
            return "stopped"
        finish_task(task, "failed", error=str(exc))
        log_event("task_failed", error=str(exc)[:300])
        return "failed"

    except Exception as exc:
        # A bug in our own code — never leave the dashboard stuck on
        # RUNNING because of it.
        finish_task(task, "failed", error=f"Unexpected crash in the agent loop: {exc!r}")
        log_event("task_failed", error=repr(exc)[:300])
        return "failed"
