"""
app.py — the web server. Start everything with:  python app.py

Flask does two jobs here:
  1. serve the dashboard page (static/index.html)
  2. answer the dashboard's polling requests by READING the plaintext
     files in agent_data/ (remember: the files are the source of truth,
     this server adds no state of its own)

The agent itself runs in a background thread, so the dashboard keeps
updating while the agent works.
"""

import json
import os
import re
import socket
import sys

from flask import Flask, jsonify, request, send_from_directory

import llm
import storage
from agent import ensure_worker, worker_busy
from narrative import LANGUAGES, narrate_task
from tools import TOOLS

app = Flask(__name__, static_folder="static")
app.json.ensure_ascii = False  # German umlauts stay readable in the browser's Network tab
PORT = int(os.getenv("PORT", "5000"))  # optional override, set in .env


def api_error(code: str, text: str, http_status: int = 400):
    """Every error the dashboard can show: an English sentence (for curl
    users and the log) plus a stable code the dashboard translates."""
    return jsonify({"error": text, "code": code}), http_status


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/state")
def api_state():
    """THE polling endpoint. The dashboard fetches this once per second
    and re-renders everything from it. Open your browser's network tab
    to watch — polling is just repeated HTTP GETs, no magic."""
    # Current content of the external screen file (may not exist yet).
    try:
        screen = json.loads(storage.MESSAGE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        screen = None

    # List of LLM call files with a one-line preview each.
    calls = []
    for f in sorted(storage.LLM_CALLS_DIR.glob("*.txt")):
        calls.append({"id": f.stem, "size": f.stat().st_size})

    # Files the agent created in its workspace (results to browse).
    workspace = []
    for f in sorted(storage.WORKSPACE_DIR.rglob("*")):
        if f.is_file():
            workspace.append(
                {
                    "name": f.relative_to(storage.WORKSPACE_DIR).as_posix(),
                    "size": f.stat().st_size,
                }
            )

    # The event log grows all day; send only a recent window. The offset
    # lets the dashboard keep its place in the full sequence.
    all_events = storage.read_events()
    events_offset = max(0, len(all_events) - 400)

    # The model the next call will use, plus the whole list for the
    # header's picker (never the API keys — only a masked hint).
    current = llm.current_model() or {}
    api_key = current.get("api_key", "")

    return jsonify(
        {
            "status": storage.read_status(),
            "config": {
                "model": current.get("model", ""),
                "endpoint": current.get("base_url", ""),
                # Empty = not set; the dashboard says so in its own language.
                "api_key_masked": (api_key[:3] + "..." + api_key[-4:]) if len(api_key) > 8 else "",
                "selected": current.get("id"),
                "models": [
                    {
                        "id": m["id"],
                        "label": m["label"],
                        "model": m["model"],
                        "endpoint": m["base_url"],
                    }
                    for m in llm.MODELS
                ],
            },
            "tools": [
                {"name": name, "description": t["description"], "args": t["args"]}
                for name, t in TOOLS.items()
            ],
            "events": all_events[events_offset:],
            "events_offset": events_offset,
            "events_total": len(all_events),
            "memories": storage.read_memories(),
            "screen": screen,
            "llm_calls": calls,
            "workspace": workspace,
            "tasks": storage.read_tasks(),
            "step_mode": storage.flag_on(storage.STEP_MODE_FILE),
            "settings": storage.read_settings(),
        }
    )


@app.route("/api/llm_calls/<call_id>")
def api_llm_call(call_id):
    """One LLM call — fetched when you click a call in the dashboard.

    The .txt file on disk is the source of truth. Here we parse it back
    into its parts (request JSON, response JSON) so the dashboard can
    render the conversation nicely. The raw text is included too."""
    # Valid ids: "003" or "003_error1" (a kept failed attempt).
    if not re.fullmatch(r"\d{3}(_error\d+)?", call_id):
        return api_error("no_such_call", "no such call", 404)
    path = storage.LLM_CALLS_DIR / f"{call_id}.txt"
    if not path.exists():
        return api_error("no_such_call", "no such call", 404)
    raw = path.read_text(encoding="utf-8")

    # The file format is:  header / --- REQUEST: ... --- / json /
    #                      --- RESPONSE (...) --- / json (or error text)
    request_json, response_json, status = None, None, ""
    url_match = re.search(r"--- REQUEST: POST (\S+) ---", raw)
    try:
        req_part = raw.split("---", 2)[2]  # after "--- REQUEST: url ---"
        req_text, resp_marker = req_part.split("--- RESPONSE (", 1)
        status, resp_text = resp_marker.split(") ---", 1)
        request_json = json.loads(req_text)
        try:
            response_json = json.loads(resp_text)
        except json.JSONDecodeError:
            response_json = resp_text.strip()  # an error message
    except (IndexError, ValueError, json.JSONDecodeError):
        pass  # unexpected format — the dashboard falls back to raw text

    return jsonify(
        {
            "id": call_id,
            "raw": raw,
            "url": url_match.group(1) if url_match else None,
            "status": status,
            "request": request_json,
            "response": response_json,
        }
    )


@app.route("/api/narrative/<int:task_id>")
def api_narrative(task_id):
    """The story of one task — fetched when you click a task row.

    Assembled live from events.jsonl and tasks.json (see narrative.py),
    so it also works for a task that is still running. ?lang=de tells it
    in German; logs/narratives.md always holds the English telling of
    every top-level task."""
    lang = request.args.get("lang", "en")
    if lang not in LANGUAGES:  # unknown = English, not an error
        lang = "en"
    story = narrate_task(task_id, lang=lang)
    if not story:
        return api_error("no_such_task", "no such task", 404)
    return jsonify({"task_id": task_id, "markdown": story})


@app.route("/api/workspace/<path:relpath>")
def api_workspace_file(relpath):
    """Content of one workspace file — same sandbox rule as the
    file_edit tool: the resolved path must stay inside workspace/."""
    full = (storage.WORKSPACE_DIR / relpath).resolve()
    if not full.is_relative_to(storage.WORKSPACE_DIR.resolve()) or not full.is_file():
        return api_error("no_such_file", "no such file", 404)
    return (
        full.read_text(encoding="utf-8", errors="replace"),
        200,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


@app.route("/api/files")
def api_files():
    """The three brain files, shown in the dashboard tabs."""
    return jsonify(
        {
            "SYSTEM_PROMPT.md": storage.SYSTEM_PROMPT_FILE.read_text(encoding="utf-8"),
            "GHOST.md": storage.GHOST_FILE.read_text(encoding="utf-8"),
            "KNOWLEDGE.md": storage.KNOWLEDGE_FILE.read_text(encoding="utf-8"),
        }
    )


@app.route("/api/task", methods=["POST"])
def api_task():
    """Add a task to the queue. If the worker is idle, wake it up —
    otherwise the task simply waits its turn (visible in the Tasks
    panel). One task runs at a time, always in order."""
    text = str((request.get_json(silent=True) or {}).get("task") or "").strip()
    if not text:
        return api_error("empty_task", "Task text is empty.")

    task = storage.add_task(text, kind="user")
    storage.log_event("task_queued", task_id=task["id"], kind="user", task=text[:200])
    ensure_worker()
    return jsonify({"ok": True, "task_id": task["id"]})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Ask the running agent to stop. It checks the flag between
    iterations, so the current LLM call still finishes first."""
    storage.set_flag(storage.STOP_FILE, True)
    return jsonify({"ok": True})


@app.route("/api/step_mode", methods=["POST"])
def api_step_mode():
    """Toggle step-by-step mode. The agent reads this flag before every
    iteration, so you can switch it on or off mid-run."""
    on = bool((request.get_json(silent=True) or {}).get("on"))
    storage.set_flag(storage.STEP_MODE_FILE, on)
    if not on:  # leaving step mode should also release a current pause
        storage.set_flag(storage.CONTINUE_FILE, True)
    return jsonify({"ok": True, "on": on})


@app.route("/api/settings", methods=["POST"])
def api_settings():
    """Change loop settings (max_iterations, verify, plan, model). Stored
    in settings.json; the agent re-reads it every round, so raising the
    round limit — or switching the model — works even while a task runs."""
    data = request.get_json(silent=True) or {}
    changes = {k: v for k, v in data.items() if k in ("max_iterations", "verify", "plan", "model")}
    # The model must be one of the entries configured in .env.
    if "model" in changes and llm.model_by_id(changes["model"]) is None:
        return api_error("unknown_model", "unknown model — see the LLM_<n>_ keys in .env")
    try:
        return jsonify({"ok": True, "settings": storage.write_settings(changes)})
    except (TypeError, ValueError):
        return api_error("invalid_settings", "invalid settings")


@app.route("/api/continue", methods=["POST"])
def api_continue():
    """'Next step' in step mode: let the agent run one more iteration."""
    storage.set_flag(storage.CONTINUE_FILE, True)
    return jsonify({"ok": True})


@app.route("/api/brain", methods=["POST"])
def api_brain_save():
    """Save one of the three brain files from the dashboard editor.
    The agent re-reads them before every LLM call, so edits apply to
    the very next iteration — even mid-task."""
    data = request.get_json(silent=True) or {}
    allowed = {
        "SYSTEM_PROMPT.md": storage.SYSTEM_PROMPT_FILE,
        "GHOST.md": storage.GHOST_FILE,
        "KNOWLEDGE.md": storage.KNOWLEDGE_FILE,
    }
    path = allowed.get(data.get("name"))
    if path is None:
        return api_error("unknown_file", "unknown file")
    storage.atomic_write_text(path, str(data.get("content", "")))
    return jsonify({"ok": True})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Clear logs, LLM calls and status. Memories and .md files survive."""
    if worker_busy():
        return api_error("busy", "Cannot reset while a task is running.")
    storage.reset_run_data()
    return jsonify({"ok": True})


@app.route("/api/reset_all", methods=["POST"])
def api_reset_all():
    """Factory reset: logs, memories, workspace, screen, brain files —
    everything back to a fresh install. Only .env survives."""
    if worker_busy():
        return api_error("busy", "Cannot reset while a task is running.")
    storage.factory_reset()
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Two servers on one port = a dashboard that shows stale data.
    # Windows sometimes allows the double bind, so we check explicitly.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
        probe.close()
        print(
            f"ERROR: something is already running on port {PORT} "
            "(another app.py?). Stop it first, or set PORT in .env."
        )
        sys.exit(1)
    probe.close()

    storage.init_agent_data()
    print()
    print("  educationclaw-agent")
    if not llm.MODELS:
        print("  Models:    (none — set BASE_URL, API_KEY and MODEL in .env!)")
    for m in llm.MODELS:
        print(
            f"  Model {m['id']}:   {m['model']} @ {m['base_url'] or '(no BASE_URL!)'}"
            f"{'  <- selected' if m is llm.current_model() else ''}"
        )
    print(f"  Dashboard: http://127.0.0.1:{PORT}")
    print()
    # use_reloader=False: the reloader would start everything twice.
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)
