"""
test_agent.py — tiny test suite, and a lesson in itself.

Run it with:      uv run python test_agent.py
(or, if you have pytest:  uv run --with pytest pytest test_agent.py)

The key trick is scripted_model() below: we replace call_llm with a FAKE
model that returns scripted replies. The whole agentic loop then runs for
real — tools, logs, status — without any network. That is how you test an
agent deterministically.

Note: the loop tests behave like real (tiny) task runs, so they write into
agent_data/logs and status.json — press "Reset logs" afterwards if you
want a clean dashboard.
"""

import json
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import agent
import storage
import tools

# ---------------------------------------------------------------------------
# parse_tool_calls — the protocol parser
# ---------------------------------------------------------------------------


def test_parse_tool_calls():
    reply = 'I will check.\n```json\n{"tool": "get_time", "args": {}}\n```'
    assert agent.parse_tool_calls(reply) == [{"tool": "get_time", "args": {}}]

    # Several calls in one reply are ALL returned — the loop runs the first
    # and tells the model the others were ignored.
    reply = (
        '```json\n{"tool": "add_task", "args": {"description": "a"}}\n```\n'
        '```json\n{"tool": "finish", "args": {}}\n```'
    )
    assert len(agent.parse_tool_calls(reply)) == 2

    # Fallback: bare JSON without a fence still parses.
    assert agent.parse_tool_calls('{"tool": "get_time", "args": {}}') != []

    # Garbage in -> empty list out (the loop sends a corrective message).
    assert agent.parse_tool_calls("no tool call here") == []
    assert agent.parse_tool_calls('```json\n{"not_a_tool": 1}\n```') == []


def test_parse_tool_call_variants():
    """Models are sloppy: uppercase fences, bare fences, prose with braces."""
    call = {"tool": "get_time", "args": {}}
    assert agent.parse_tool_calls('```JSON\n{"tool": "get_time", "args": {}}\n```') == [call]
    assert agent.parse_tool_calls('```\n{"tool": "get_time", "args": {}}\n```') == [call]
    # A stray "{" in the prose before the real call must not hide it.
    reply = 'The format is {tool, args}. Here:\n{"tool": "get_time", "args": {}}'
    assert agent.parse_tool_calls(reply) == [call]
    # Nested braces inside the call are fine.
    reply = '```json\n{"tool": "set_screen", "args": {"message": "hi {x}", "mood": "happy"}}\n```'
    assert agent.parse_tool_calls(reply)[0]["args"]["message"] == "hi {x}"


def test_parse_tool_call_recovery():
    """Slips seen in real logs: raw newlines inside JSON strings, a ')'
    closing the call instead of '}', and Mistral's native syntax."""
    got = agent.parse_tool_calls(
        '```json\n{"tool": "file_edit", "args": '
        '{"action": "write", "path": "a.txt", "content": "line 1\nline 2"}}\n```'
    )
    assert got[0]["args"]["content"] == "line 1\nline 2"
    got = agent.parse_tool_calls('```json\n{"tool": "get_time", "args": {})\n```')
    assert got == [{"tool": "get_time", "args": {}}]
    got = agent.parse_tool_calls(
        "Next step: write it.[TOOL_CALLS]file_edit"
        '{"action": "write", "path": "count.txt", "content": "5"}'
    )
    assert got == [
        {"tool": "file_edit", "args": {"action": "write", "path": "count.txt", "content": "5"}}
    ]
    # The reminder is appended to every message the orchestrator sends.
    assert agent.user_message("TOOL RESULT")["content"].endswith("```.)")


# ---------------------------------------------------------------------------
# Tools — validation and the sandbox
# ---------------------------------------------------------------------------


def test_set_screen():
    original = tools.MESSAGE_FILE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tools.MESSAGE_FILE = Path(tmp) / "message.json"

            assert "Screen updated" in tools.set_screen("Hello!", "happy")
            data = json.loads(tools.MESSAGE_FILE.read_text(encoding="utf-8"))
            assert data == {"message": "Hello!", "mood": "happy"}

            # Contract violations come back as error STRINGS, never crashes.
            assert tools.set_screen("Hi", "angry").startswith("TOOL ERROR")
            assert tools.set_screen("Hi \U0001f600", "happy").startswith("TOOL ERROR")
    finally:
        tools.MESSAGE_FILE = original


def test_file_edit_sandbox():
    original = tools.WORKSPACE_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tools.WORKSPACE_DIR = Path(tmp)

            assert "Wrote" in tools.file_edit("write", "a.txt", "hello")
            assert tools.file_edit("read", "a.txt") == "hello"
            assert "a.txt" in tools.file_edit("list")

            # The important part: escaping the sandbox must fail.
            assert tools.file_edit("read", "../secret.txt").startswith("TOOL ERROR")
            assert tools.file_edit("write", "..\\evil.txt", "x").startswith("TOOL ERROR")
    finally:
        tools.WORKSPACE_DIR = original


def test_file_edit_nested_and_errors():
    original = tools.WORKSPACE_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tools.WORKSPACE_DIR = Path(tmp)
            assert "Wrote" in tools.file_edit("write", "site/index.html", "<h1>hi</h1>")
            assert "site/index.html" in tools.file_edit("list")  # nested files listed
            assert tools.file_edit("read", "site").startswith("TOOL ERROR")  # a folder
            assert tools.file_edit("read", "").startswith("TOOL ERROR")  # no path
            assert tools.file_edit("rename", "a").startswith("TOOL ERROR")  # bad action
            assert "Deleted" in tools.file_edit("delete", "site/index.html")
            assert tools.file_edit("list").endswith("(empty)")
    finally:
        tools.WORKSPACE_DIR = original


def test_run_tool_guardrails():
    assert tools.run_tool("no_such_tool", {}).startswith("TOOL ERROR")
    assert tools.run_tool("set_screen", {"wrong_arg": 1}).startswith("TOOL ERROR")


# ---------------------------------------------------------------------------
# Settings — loop depth is just a number in a file
# ---------------------------------------------------------------------------


def test_settings():
    storage.init_agent_data()
    saved = storage.read_settings()
    try:
        assert storage.write_settings({"max_iterations": 7})["max_iterations"] == 7
        assert storage.read_settings()["max_iterations"] == 7
        # Out-of-range values get clamped, junk raises (handled by the API).
        assert storage.write_settings({"max_iterations": 999})["max_iterations"] == 50
    finally:
        storage.write_settings(saved)


# ---------------------------------------------------------------------------
# The agentic loop — run it for real, against a scripted fake model
# ---------------------------------------------------------------------------


@contextmanager
def scripted_model(replies, seen=None):
    """Replace call_llm with a fake that returns the scripted replies in
    order. If `seen` is a list, the last message of every request is
    recorded there — that is what the model would have read."""
    original = agent.call_llm

    def fake(messages):
        if seen is not None:
            seen.append(messages[-1]["content"])
        return replies.pop(0)

    agent.call_llm = fake
    try:
        yield
    finally:
        agent.call_llm = original


@contextmanager
def clean_run(**settings):
    """A fresh queue and event log, step mode off, and settings pinned to
    defaults (verify/plan off) unless a test overrides them — whatever the
    dashboard last set must not leak into tests. Restored afterwards."""
    storage.init_agent_data()
    storage.reset_run_data()
    saved = storage.read_settings()
    storage.write_settings({"verify": False, "plan": False, **settings})
    storage.set_flag(storage.STEP_MODE_FILE, False)
    try:
        yield
    finally:
        storage.write_settings(saved)


def run_scripted(task, replies, **settings):
    """Queue one task and run the real worker loop against the fake model.
    The worker also processes any tasks the orchestrator queues itself
    (subtasks, verification tasks). Returns the queued task."""
    with clean_run(**settings), scripted_model(replies):
        queued = storage.add_task(task, kind="user")
        agent.run_queue()
    return queued


def task_by_id(task_id):
    return next(t for t in storage.read_tasks() if t["id"] == task_id)


def test_agent_loop():
    queued = run_scripted(
        "What time is it?",
        [
            'Checking.\n```json\n{"tool": "get_time", "args": {}}\n```',
            'Done!\n```json\n{"tool": "finish", "args": {"summary": "told the time"}}\n```',
        ],
    )
    task = task_by_id(queued["id"])
    assert task["status"] == "done", task
    assert task["result"] == "told the time"
    types = [e["type"] for e in storage.read_events()]
    assert "tool_call" in types and "task_done" in types


def test_multiple_calls_first_only():
    """A reply with several tool calls executes ONLY the first one; the
    model is told the rest were ignored and must send them again."""
    replies = [
        # Two calls at once: get_time runs, finish is ignored...
        '```json\n{"tool": "get_time", "args": {}}\n```\n'
        '```json\n{"tool": "finish", "args": {"summary": "done"}}\n```',
        # ...so the model has to send finish again, alone.
        'Done.\n```json\n{"tool": "finish", "args": {"summary": "told it"}}\n```',
    ]
    seen = []
    with clean_run(), scripted_model(replies, seen):
        queued = storage.add_task("Say the time.", kind="user")
        agent.run_queue()

    task = task_by_id(queued["id"])
    assert task["status"] == "done" and task["result"] == "told it"
    events = storage.read_events()
    perrs = [e for e in events if e["type"] == "protocol_error"]
    assert perrs and perrs[0]["count"] == 2
    assert len([e for e in events if e["type"] == "tool_call"]) == 1
    # The tool result carried the warning about the ignored call.
    assert "TOOL RESULT (get_time)" in seen[1] and "IGNORED" in seen[1]


def test_finish_batched_with_others_is_refused():
    """'finish' first, followed by more calls: nothing runs, the model is
    sent back — finishing would silently drop the other calls."""
    queued = run_scripted(
        "Say the time.",
        [
            '```json\n{"tool": "finish", "args": {"summary": "early"}}\n```\n'
            '```json\n{"tool": "get_time", "args": {}}\n```',
            'Ok.\n```json\n{"tool": "get_time", "args": {}}\n```',
            'Done.\n```json\n{"tool": "finish", "args": {"summary": "told it"}}\n```',
        ],
    )
    task = task_by_id(queued["id"])
    assert task["status"] == "done" and task["result"] == "told it"
    assert len([e for e in storage.read_events() if e["type"] == "tool_call"]) == 1


def test_subtask_inference():
    """The model can split work using the add_task tool. The subtask must
    run BEFORE a user task that was queued later (children first)."""
    replies = [
        # t1 delegates part B, does nothing else, finishes
        'Splitting.\n```json\n{"tool": "add_task", '
        '"args": {"description": "Do part B: say the time."}}\n```',
        'Delegated.\n```json\n{"tool": "finish", "args": {"summary": "queued part B"}}\n```',
        # the subtask must come next (before t2!)
        'Part B.\n```json\n{"tool": "get_time", "args": {}}\n```',
        'Done.\n```json\n{"tool": "finish", "args": {"summary": "part B done"}}\n```',
        # only now t2
        'Easy.\n```json\n{"tool": "finish", "args": {"summary": "second task done"}}\n```',
    ]
    with clean_run(), scripted_model(replies):
        t1 = storage.add_task("Big job: do part A and part B.", kind="user")
        t2 = storage.add_task("Unrelated second user task.", kind="user")
        agent.run_queue()

    tasks = storage.read_tasks()
    sub = [t for t in tasks if t["kind"] == "subtask"][0]
    assert sub["parent"] == t1["id"] and sub["status"] == "done"
    assert all(t["status"] == "done" for t in tasks), tasks
    # Execution order from the event log: t1, subtask, then t2.
    started = [e["task"] for e in storage.read_events() if e["type"] == "task_start"]
    assert started == [t1["task"], "Do part B: say the time.", t2["task"]], started


def test_stop_cancels_queue():
    """Stop pressed mid-run: the current task ends as 'stopped' and every
    queued task is cancelled too — the model is not called again."""
    calls = []

    def fake(messages):
        # Simulate the user pressing Stop while the model is answering.
        calls.append(1)
        storage.set_flag(storage.STOP_FILE, True)
        return 'ok\n```json\n{"tool": "get_time", "args": {}}\n```'

    original = agent.call_llm
    agent.call_llm = fake
    try:
        with clean_run():
            t1 = storage.add_task("First task.", kind="user")
            t2 = storage.add_task("Second task.", kind="user")
            agent.run_queue()
    finally:
        agent.call_llm = original

    tasks = {t["id"]: t for t in storage.read_tasks()}
    assert tasks[t1["id"]]["status"] == "stopped", tasks
    assert tasks[t2["id"]]["status"] == "stopped", tasks
    assert len(calls) == 1  # round 1 of task 1 only — nothing after Stop


def test_restart_recovery():
    """After a (simulated) server restart, 'running' tasks become failed
    and 'queued' ones are cancelled — no ghost tasks."""
    with clean_run():
        t1 = storage.add_task("Was running when the server died.", kind="user")
        t2 = storage.add_task("Was still waiting.", kind="user")
        storage.update_task(t1["id"], status="running")

        storage.init_agent_data()  # what app.py does on startup

    tasks = {t["id"]: t for t in storage.read_tasks()}
    assert tasks[t1["id"]]["status"] == "failed"
    assert "restarted" in tasks[t1["id"]]["error"]
    assert tasks[t2["id"]]["status"] == "stopped"


def test_plan_first_mode():
    """With plan-first on, a user task is a pure planner: real tools are
    refused, finishing with an empty plan is refused, and the work only
    happens in the subtasks it creates."""
    queued = run_scripted(
        "Say the time on the screen.",
        [
            # 1. tries to do the work itself -> orchestrator refuses
            'Easy.\n```json\n{"tool": "get_time", "args": {}}\n```',
            # 2. tries to finish without a plan -> sent back
            'Fine.\n```json\n{"tool": "finish", "args": {"summary": "nothing to do"}}\n```',
            # 3. plans properly
            'Ok.\n```json\n{"tool": "add_task", "args": {"description": '
            '"Get the time and show it on the screen with a happy mood."}}\n```',
            'Planned.\n```json\n{"tool": "finish", "args": {"summary": "1 subtask queued"}}\n```',
            # 4. the subtask runs (fresh) and may use real tools again
            'Doing it.\n```json\n{"tool": "get_time", "args": {}}\n```',
            'Done.\n```json\n{"tool": "finish", "args": {"summary": "shown"}}\n```',
        ],
        plan=True,
    )

    tasks = storage.read_tasks()
    planner = task_by_id(queued["id"])
    sub = [t for t in tasks if t["kind"] == "subtask"][0]
    assert planner["status"] == "done" and sub["status"] == "done", tasks
    events = storage.read_events()
    results = [e["result"] for e in events if e["type"] == "tool_result"]
    # The planner's get_time was refused; the subtask's get_time ran.
    assert any("planning mode" in r for r in results), results
    assert any(r.startswith("It is") for r in results), results
    assert any(e["type"] == "planning_incomplete" for e in events)


def test_verification_task():
    # Finishing the user task must queue a verification task, which the
    # worker then runs as a fresh agent run (replies 3 and 4 below).
    queued = run_scripted(
        "What time is it?",
        [
            'Checking.\n```json\n{"tool": "get_time", "args": {}}\n```',
            'Done!\n```json\n{"tool": "finish", "args": {"summary": "told the time"}}\n```',
            'Let me check.\n```json\n{"tool": "get_time", "args": {}}\n```',
            'Confirmed.\n```json\n{"tool": "finish", '
            '"args": {"summary": "VERIFIED: time was told"}}\n```',
        ],
        verify=True,
    )

    tasks = storage.read_tasks()
    vtasks = [t for t in tasks if t["kind"] == "verification" and t["parent"] == queued["id"]]
    assert len(vtasks) == 1, tasks
    assert vtasks[0]["status"] == "done"
    assert vtasks[0]["result"].startswith("VERIFIED")
    # A verification task must NOT spawn another verification task.
    assert len([t for t in tasks if t["kind"] == "verification"]) == 1
    assert "task_queued" in [e["type"] for e in storage.read_events()]


def test_worker_thread_handoff():
    """The background worker (what app.py starts) drains the queue and
    retires; a later submission starts a fresh one. No task is stranded."""
    replies = [
        'Done.\n```json\n{"tool": "finish", "args": {"summary": "first"}}\n```',
        'Done.\n```json\n{"tool": "finish", "args": {"summary": "second"}}\n```',
    ]
    with clean_run(), scripted_model(replies):
        for n in (1, 2):
            t = storage.add_task(f"Task number {n}.", kind="user")
            agent.ensure_worker()
            deadline = time.time() + 10
            while agent.worker_busy() and time.time() < deadline:
                time.sleep(0.05)
            assert not agent.worker_busy(), "the worker did not retire"
            assert task_by_id(t["id"])["status"] == "done"
    assert not replies


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("\nAll tests passed.")
