# educationclaw-agent — a radically transparent teaching agent

A small but real agentic AI system for workshops. Everything the agent
does becomes a plaintext file in `agent_data/` the moment it happens —
the web dashboard is just a viewer over those files.

## The idea

An agent is a while-loop around an LLM:

```
while not done:
    1. build the context   (system prompt + conversation so far)
    2. ask the LLM         "what should happen next?"
    3. the LLM answers with a tool call, e.g. {"tool": "get_time", "args": {}}
    4. run that tool, append the result to the conversation
    5. repeat — until the LLM calls the special tool "finish"
```

Memory, knowledge and persona are just text pasted into step 1.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and any OpenAI-compatible
endpoint (OpenAI, Ollama, LM Studio, OpenRouter, ...).

```
copy .env.example .env    # then edit .env: BASE_URL, API_KEY, MODEL
uv run app.py
```

Open http://127.0.0.1:5000 (set `PORT` in `.env` to change it).

To compare models, add more of them to `.env` with an `LLM_<n>_` prefix
(`LLM_2_BASE_URL`, `LLM_2_API_KEY`, `LLM_2_MODEL`, optional `LLM_2_LABEL`).
Keys a numbered model leaves out fall back to the plain ones, so a second
model at the same provider needs only its `MODEL` line — see
`.env.example`. The dashboard header then shows a model picker; the
choice is stored as `model` in `settings.json` and applies to the next
LLM call, even mid-task.

Run the tests with `uv run python test_agent.py`; lint and format with
`uv run --with ruff ruff check .` and `uv run --with ruff ruff format .`.

## Source files

| File            | What it is                                                 |
| --------------- | ---------------------------------------------------------- |
| `agent.py`      | The agentic loop — start reading here                      |
| `llm.py`        | One HTTP POST per LLM call, fully logged; models from .env |
| `tools.py`      | The tool registry — add your own tool here                 |
| `narrative.py`  | Retells a task's run as a plain-English story from the log |
| `storage.py`    | Paths and file plumbing (logs, status, tasks, flags)       |
| `app.py`        | Flask server: serves the dashboard, reads the files        |
| `static/`       | The dashboard: one HTML page, CSS, vanilla JS              |
| `test_agent.py` | Runs the real loop against a scripted fake model           |

## The agent's brain: `agent_data/`

| File / folder        | What it is                                                          |
| -------------------- | ------------------------------------------------------------------- |
| `SYSTEM_PROMPT.md`   | The rules of the game, incl. the tool-call protocol                 |
| `GHOST.md`           | The agent's persona                                                 |
| `KNOWLEDGE.md`       | Facts injected into every LLM call                                  |
| `memory/`            | One markdown file per memory the agent chose to save                |
| `logs/events.jsonl`  | Every orchestrator action, one JSON line each                       |
| `logs/narratives.md` | The story of every top-level task (1, 2, 3, …), retold from the log |
| `llm_calls/NNN.txt`  | The full request + response of every single LLM call                |
| `tasks.json`         | The task queue and record (subtasks, verifications)                 |
| `status.json`        | What the agent is doing right now                                   |
| `settings.json`      | Loop settings (max rounds, plan first, self-verification, model)    |
| `workspace/`         | The only folder the `file_edit` tool may touch                      |
| `message.json`       | The "screen": `{"message": "...", "mood": "neutral|happy|sad"}` |

The three `.md` files are re-read before every LLM call — edit them
(in an editor or in the dashboard) while a task runs and the next call
already contains the change.

## Tools

`get_time`, `set_screen`, `file_edit` (sandboxed to `workspace/`),
`add_task` (split work into subtasks that each run as a fresh agent run),
`save_memory`, and the pseudo-tool `finish` that ends the loop.

Adding a tool = one function + one entry in `TOOLS` in `tools.py`.
The system prompt, the executor and the dashboard all read that registry.

## Dashboard controls

- **Step-by-step** — pause before every loop round until you press *Next step*.
- **Plan first** — user tasks become pure planners: only `add_task` is
  allowed, the real work happens in the subtasks.
- **Self-verification** — every finished user task queues a fresh
  verification run that checks the work with its own tools.
- **max rounds** — the loop cap, re-read every round.
- **Model picker** (in the header, once `.env` has several models) —
  which model the next LLM call goes to. Switching mid-task is allowed;
  the story of the task names every model that answered.
- **Stop** — cancels the running task and everything still queued.
- **Reset logs / Reset everything** — clear the run data / full factory
  reset (only `.env` survives).

The buttons are just flag files (`stop.flag`, `step_mode.flag`,
`continue.flag`) that the agent checks between rounds.

## The story of a task

Click any row in the *Tasks* panel and the dialog ends with **The story**:
the run retold round by round — what the model thought, which tool it
called with which arguments, what came back — followed by the same for
every subtask and verification the task spawned, and the outcome (rounds,
duration, LLM calls, tokens). It is assembled by `narrative.py` purely
from `events.jsonl` and `tasks.json`, so it can never claim something
that did not happen. `logs/narratives.md` holds the story of every
top-level task and is rewritten each time a task ends.

## Demo task

> Check the current time, then set the screen to a happy greeting that
> mentions the time, and save a memory that you greeted the workshop.

Open **LLM call #001** to see the full prompt, watch the tool calls in
the event log, then open the *last* call: its request now contains the
new memory — the memory loop, proven.
