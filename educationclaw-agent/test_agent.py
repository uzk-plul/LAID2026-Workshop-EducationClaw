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
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from string import Formatter

import agent
import llm
import narrative
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
# Models — several provider/model combinations in .env, one picked at a time
# ---------------------------------------------------------------------------


def test_i18n_tables():
    """The dashboard's own words live in static/i18n.js: every key exists in
    both languages with the same placeholders, every key the page uses is
    defined (and nothing is defined that nobody uses), and the English event
    sentences are exactly what storage.describe_event() writes into
    events.jsonl — the one place where the two sides must agree."""
    static = storage.PROJECT_DIR / "static"
    src = (static / "i18n.js").read_text(encoding="utf-8")
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)  # full-line comments are allowed
    try:
        table, _ = json.JSONDecoder().raw_decode(src, src.index("{", src.index("const I18N")))
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"i18n.js must stay JSON-compatible (double quotes, no trailing commas): {exc}"
        ) from exc

    def placeholders(text):
        if isinstance(text, dict):  # a plural entry: {"one": ..., "other": ...}
            assert set(text) == {"one", "other"}
            return placeholders(text["one"]) | placeholders(text["other"])
        return set(re.findall(r"\{(\w+)\}", text))

    for key, entry in table.items():
        assert set(entry) == {"en", "de"}, key
        assert placeholders(entry["en"]) == placeholders(entry["de"]), key

    html = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "dashboard.js").read_text(encoding="utf-8")
    used = set(re.findall(r'data-i18n(?:-title|-placeholder)?="([^"]+)"', html))
    used |= set(re.findall(r'\bt(?:Html)?\("([a-z]+(?:\.[a-z0-9_]+)+)"', js))
    # Keys built at runtime ("status." + key, "event." + e.type, ...): every
    # quoted dotted string in dashboard.js that starts with one of the
    # table's areas (so CSS selectors like "section.panel" don't count),
    # plus the enum values behind the runtime-built ones.
    areas = {key.split(".")[0] for key in table}
    used |= {k for k in re.findall(r'"([a-z]+(?:\.[a-z0-9_]+)+)"', js) if k.split(".")[0] in areas}
    families = {
        "status": [
            "idle",
            "running",
            "paused",
            "done",
            "failed",
            "stopped",
            "verifying",
            "planning",
        ],
        "mood": ["neutral", "happy", "sad"],
        "kind": ["user", "subtask", "verification"],
        "timeline.kind": ["task", "verify", "plan"],
        "event": ["task_done", "task_failed", "task_stopped", "paused"],
        "error": [
            "no_such_call",
            "no_such_task",
            "no_such_file",
            "empty_task",
            "unknown_model",
            "invalid_settings",
            "unknown_file",
            "busy",
        ],
        "outcome": ["max_rounds", "stopped", "cancelled_stop", "restart_running", "restart_queued"],
    }
    for prefix, values in families.items():
        used |= {f"{prefix}.{v}" for v in values}
    assert used <= set(table), f"missing in i18n.js: {sorted(used - set(table))}"
    assert set(table) <= used, f"unused in i18n.js: {sorted(set(table) - used)}"

    # The English event templates must say exactly what the server writes.
    samples = [
        ("task_start", {"kind": "verification"}, "event.task_start.verification"),
        ("task_start", {"kind": "user", "planning": True}, "event.task_start.planning"),
        ("task_start", {"kind": "user", "planning": False}, "event.task_start"),
        ("planning_incomplete", {}, "event.planning_incomplete"),
        ("task_queued", {"kind": "verification"}, "event.task_queued.verification"),
        ("task_queued", {"kind": "subtask"}, "event.task_queued.subtask"),
        ("task_queued", {"kind": "user"}, "event.task_queued"),
        ("iteration_start", {"iteration": 3}, "event.iteration_start"),
        ("llm_call_start", {"model": "gpt-x", "call_id": 3}, "event.llm_call_start"),
        (
            "llm_call_done",
            {"duration": 1.2, "tokens_in": 10, "tokens_out": 3},
            "event.llm_call_done.tokens",
        ),
        ("llm_call_done", {"duration": 1.2}, "event.llm_call_done"),
        ("llm_call_error", {"attempt": 1}, "event.llm_call_error.first"),
        ("llm_call_error", {"attempt": 2}, "event.llm_call_error.again"),
        ("tool_call", {"tool": "get_time"}, "event.tool_call"),
        (
            "tool_result",
            {"tool": "set_screen", "result": "TOOL ERROR: no"},
            "event.tool_result.refused",
        ),
        ("tool_result", {"tool": "get_time", "result": "It is"}, "event.tool_result"),
        ("protocol_error", {"count": 2}, "event.protocol_error.many"),
        ("protocol_error", {"count": 0}, "event.protocol_error"),
        ("task_done", {}, "event.task_done"),
        ("task_failed", {}, "event.task_failed"),
        ("task_stopped", {}, "event.task_stopped"),
        ("paused", {}, "event.paused"),
    ]

    def render(template, fields):
        return re.sub(r"\{(\w+)\}", lambda m: str(fields.get(m.group(1), m.group(0))), template)

    for event_type, fields, key in samples:
        assert render(table[key]["en"], fields) == storage.describe_event(event_type, fields), key
    # describe_event says "the model" when no model name is known — so does the dashboard.
    assert table["event.the_model"]["en"] == "the model"


def test_load_models():
    """The plain keys are model 1 (an old .env keeps working); LLM_<n>_
    keys add more and inherit whatever they leave out from the plain keys."""
    env = {
        "BASE_URL": "https://api.openai.com/v1/",
        "API_KEY": "sk-1",
        "MODEL": "gpt-4o-mini",
        "LLM_2_MODEL": "gpt-4o",  # same provider: only the model line
        "LLM_2_LABEL": "the big one",
        "LLM_3_BASE_URL": "http://localhost:11434/v1",
        "LLM_3_API_KEY": "ollama",
        "LLM_3_MODEL": "llama3.2",
        "LLM_3_TEMPERATURE": "0.2",
    }
    m1, m2, m3 = llm.load_models(env)
    assert (m1["id"], m1["model"], m1["label"]) == ("1", "gpt-4o-mini", "gpt-4o-mini")
    assert m1["base_url"] == "https://api.openai.com/v1"  # trailing slash dropped
    assert (m2["base_url"], m2["api_key"]) == (m1["base_url"], "sk-1")  # inherited
    assert (m2["model"], m2["label"]) == ("gpt-4o", "the big one")
    assert (m3["base_url"], m3["api_key"]) == ("http://localhost:11434/v1", "ollama")
    assert m3["temperature"] == 0.2 and m3["max_tokens"] is None
    assert m1["temperature"] is None

    # The classic single-model .env — and no configuration at all.
    only = llm.load_models({"BASE_URL": "http://x/v1", "API_KEY": "k", "MODEL": "m"})
    assert [(m["id"], m["model"]) for m in only] == [("1", "m")]
    assert llm.load_models({}) == []

    # The same model name at two endpoints gets labels that tell them apart.
    twins = llm.load_models(
        {
            "BASE_URL": "https://a.example/v1",
            "API_KEY": "k",
            "MODEL": "m",
            "LLM_2_BASE_URL": "http://localhost:1234/v1",
            "LLM_2_MODEL": "m",
        }
    )
    assert [m["label"] for m in twins] == ["m @ a.example", "m @ localhost:1234"]


def test_model_selection():
    """The dashboard's pick is just "model" in settings.json: the next call
    goes to that entry, an unknown pick falls back to the first model —
    and the call file and the event log record which model answered."""
    saved_models, saved_post = llm.MODELS, llm.requests.post
    llm.MODELS = llm.load_models(
        {
            "BASE_URL": "https://api.openai.com/v1",
            "API_KEY": "sk-1",
            "MODEL": "gpt-4o-mini",
            "LLM_2_BASE_URL": "http://localhost:11434/v1",
            "LLM_2_API_KEY": "ollama",
            "LLM_2_MODEL": "llama3.2",
        }
    )
    sent = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append((url, headers["Authorization"], json["model"]))

        class Response:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "hi"}}], "usage": {}}

        return Response()

    llm.requests.post = fake_post
    try:
        with clean_run():
            storage.write_settings({"model": "2"})
            assert llm.current_model()["id"] == "2"
            assert llm.call_llm([{"role": "user", "content": "hello"}]) == "hi"
            storage.write_settings({"model": "no-such-model"})
            assert llm.current_model()["id"] == "1"
            llm.call_llm([{"role": "user", "content": "hello"}])
            on_disk = (storage.LLM_CALLS_DIR / "001.txt").read_text(encoding="utf-8")
            events = storage.read_events()
    finally:
        llm.MODELS, llm.requests.post = saved_models, saved_post

    assert sent == [
        ("http://localhost:11434/v1/chat/completions", "Bearer ollama", "llama3.2"),
        ("https://api.openai.com/v1/chat/completions", "Bearer sk-1", "gpt-4o-mini"),
    ]
    assert "POST http://localhost:11434/v1/chat/completions" in on_disk
    done = [e for e in events if e["type"] == "llm_call_done"]
    assert [e["model"] for e in done] == ["llama3.2", "gpt-4o-mini"]
    # Even the plain-English line in the log names the model.
    assert any("conversation to llama3.2" in e["note"] for e in events)


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
    # The English sentence stays on disk; the code lets the dashboard translate it.
    assert tasks[t1["id"]]["error_code"] == "stopped"
    assert tasks[t2["id"]]["error_code"] == "cancelled_stop"


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
    assert tasks[t1["id"]]["error_code"] == "restart_running"
    assert tasks[t2["id"]]["error_code"] == "restart_queued"


def test_round_limit():
    """The loop cap ends a task that never finishes — as 'failed', with an
    error_code next to the English sentence so the dashboard can translate it."""
    queued = run_scripted(
        "Never finish.",
        ['Again.\n```json\n{"tool": "get_time", "args": {}}\n```'] * 2,  # exactly 2 rounds
        max_iterations=2,
    )
    task = task_by_id(queued["id"])
    assert task["status"] == "failed" and task["error_code"] == "max_rounds", task
    assert "Gave up after 2 rounds" in task["error"]
    assert task["iterations"] == 2
    # The story translates the code; the English telling keeps the stored sentence.
    assert "Nach 2 Runden aufgegeben" in narrative.narrate_task(queued["id"], lang="de")
    assert "Gave up after 2 rounds" in narrative.narrate_task(queued["id"])


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


# The scripted run test_narrative retells — shared with the German test so
# both look at the very same story.
GREETING_REPLIES = [
    'First the time.\n```json\n{"tool": "get_time", "args": {}}\n```',
    'Try a mood.\n```json\n{"tool": "set_screen", "args": {"message": "Hi", "mood": "angry"}}\n```',
    'Delegate the rest.\n```json\n{"tool": "add_task", '
    '"args": {"description": "Save a memory titled Greeted."}}\n```',
    'All queued.\n```json\n{"tool": "finish", "args": {"summary": "greeted"}}\n```',
    # the subtask
    'Saving.\n```json\n{"tool": "save_memory", '
    '"args": {"title": "Greeted", "content": "yes"}}\n```',
    'Done.\n```json\n{"tool": "finish", "args": {"summary": "memory saved"}}\n```',
]


def run_greeting_story():
    """Run GREETING_REPLIES through the real loop (memories go to a temp
    folder) and return (task_id, tasks, events) as they are on disk."""
    original = tools.MEMORY_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tools.MEMORY_DIR = Path(tmp)
            queued = run_scripted("Greet the workshop.", list(GREETING_REPLIES))  # a copy: pop()
    finally:
        tools.MEMORY_DIR = original
    return queued["id"], storage.read_tasks(), storage.read_events()


def test_narrative():
    """The story of a task is retold from the log: the model's thought,
    each tool call with its result, refused calls, subtasks nested under
    their parent — and logs/narratives.md is rewritten when a task ends."""
    task_id, tasks, events = run_greeting_story()
    story = narrative.narrate_task(task_id, tasks, events)
    assert story.startswith("## Task 1 — Greet the workshop.")
    assert "done (4 rounds" in story and '"greeted"' in story
    assert 'Round 1: thought "First the time." → called **get_time**() → It is' in story
    assert 'Round 2: thought "Try a mood." → called **set_screen**(' in story
    assert 'message="Hi", mood="angry")' in story
    assert "**refused:** mood 'angry' is not allowed" in story
    assert 'queued subtask 1.1: "Save a memory titled Greeted."' in story
    assert 'Round 4: thought "All queued." → called **finish** → "greeted"' in story
    # The subtask is told underneath, indented one level.
    assert "  ### Subtask 1.1 — Save a memory titled Greeted." in story
    assert '  - Round 1: thought "Saving." → called **save_memory**(' in story
    # The file on disk tells the same story for every top-level task.
    on_disk = storage.NARRATIVES_FILE.read_text(encoding="utf-8")
    assert on_disk.startswith("# Narratives") and story.strip() in on_disk
    # A task that never ran still gets a (short) story.
    assert narrative.narrate_task(9999) == ""


def test_narrative_german():
    """The same story in German: every sentence of the narrator is
    translated, everything QUOTED from the run (task text, thoughts, tool
    results) stays as it was — and the markdown skeleton is identical, so
    the dashboard renders both the same way. The file on disk stays English."""
    task_id, tasks, events = run_greeting_story()
    en = narrative.narrate_task(task_id, tasks, events)
    de = narrative.narrate_task(task_id, tasks, events, lang="de")
    assert de.startswith("## Aufgabe 1 — Greet the workshop.")
    assert "**Ergebnis:** erledigt (4 Runden" in de and '"greeted"' in de
    assert 'Runde 1: dachte "First the time." → rief **get_time**() auf → It is' in de
    assert "**verweigert:** mood 'angry' is not allowed" in de  # tool text stays English
    assert 'Teilaufgabe 1.1 eingereiht: "Save a memory titled Greeted."' in de
    assert 'Runde 4: dachte "All queued." → rief **finish** auf → "greeted"' in de
    assert "  ### Teilaufgabe 1.1 — Save a memory titled Greeted." in de
    assert '  - Runde 1: dachte "Saving." → rief **save_memory**(' in de

    def skeleton(story):  # headings, bullets and indentation — nothing else
        return [re.match(r" *(#+ |- |)", line).group(0) for line in story.splitlines()]

    assert skeleton(de) == skeleton(en)
    assert narrative.narrate_task(task_id, tasks, events, lang="xx") == en  # unknown = English
    assert narrative.narrate_all(tasks, events, lang="de").startswith("# Geschichten — eine pro")
    assert storage.NARRATIVES_FILE.read_text(encoding="utf-8").startswith("# Narratives")


def test_narrative_numbers():
    """Plurals and thousands separators differ per language. The scripted
    model bypasses call_llm, so _outcome is checked directly with fake usage."""
    task = {"status": "done", "iterations": 1, "result": "ok"}
    run = [{"type": "llm_call_done", "tokens_in": 12345, "tokens_out": 6, "model": "m"}]
    en = narrative._outcome(task, run)
    de = narrative._outcome(task, run, lang="de")
    assert "1 round, 1 LLM call, 12,345 tokens read / 6 written, model m" in en
    assert "1 Runde, 1 LLM-Aufruf, 12.345 Tokens gelesen / 6 geschrieben, Modell m" in de
    # A known error_code is translated; the English telling keeps the stored sentence.
    task = {"status": "failed", "iterations": 3, "error": "Gave up.", "error_code": "max_rounds"}
    assert narrative._outcome(task, []) == "failed (3 rounds) — Gave up."
    assert "Nach 3 Runden aufgegeben" in narrative._outcome(task, [], lang="de")
    # No code (an LLM error, a crash): the sentence is shown as stored in every language.
    task = {"status": "failed", "error": "connection refused"}
    assert "connection refused" in narrative._outcome(task, [], lang="de")


def test_narrative_strings_parity():
    """Every sentence exists in both languages with the same placeholders —
    otherwise a missing translation would only show up at render time."""

    def placeholders(template):
        return {name for _, name, _, _ in Formatter().parse(template) if name}

    assert set(narrative.LANGUAGES) == {"en", "de"}
    for key, entry in narrative.STRINGS.items():
        assert set(entry) == {"en", "de"}, key
        assert placeholders(entry["en"]) == placeholders(entry["de"]), key
    assert set(narrative.OUTCOMES) == {
        "max_rounds",
        "stopped",
        "cancelled_stop",
        "restart_running",
        "restart_queued",
    }


def test_api_narrative_and_errors():
    """The story endpoint speaks both languages (?lang=de), and every error
    the dashboard can show carries a stable code next to its English text.
    Only error paths are exercised: a valid POST would start the real worker."""
    import app as web  # safe: the port probe and the startup are under __main__

    client = web.app.test_client()
    reply = 'Hi.\n```json\n{"tool": "finish", "args": {"summary": "hi"}}\n```'
    queued = run_scripted("Say hi.", [reply])
    en = client.get(f"/api/narrative/{queued['id']}").get_json()["markdown"]
    de = client.get(f"/api/narrative/{queued['id']}?lang=de").get_json()["markdown"]
    assert en.startswith("## Task 1") and de.startswith("## Aufgabe 1")
    assert client.get(f"/api/narrative/{queued['id']}?lang=xx").get_json()["markdown"] == en
    res = client.get("/api/narrative/9999")
    assert res.status_code == 404 and res.get_json()["code"] == "no_such_task"
    res = client.post("/api/task", json={"task": "  "})
    assert res.status_code == 400 and res.get_json()["code"] == "empty_task"
    assert res.get_json()["error"] == "Task text is empty."  # the sentence is still there
    res = client.post("/api/settings", json={"model": "no-such-model"})
    assert res.status_code == 400 and res.get_json()["code"] == "unknown_model"
    res = client.get("/api/llm_calls/zzz")
    assert res.status_code == 404 and res.get_json()["code"] == "no_such_call"


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
