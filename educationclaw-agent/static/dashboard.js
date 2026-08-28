/*
 * dashboard.js — the whole dashboard in plain JavaScript.
 *
 * The mechanism is deliberately primitive: once per second we fetch
 * /api/state (one GET request — watch it in the browser's Network tab)
 * and re-render every panel from that single JSON payload.
 * The server, in turn, only reads plaintext files. No websockets,
 * no framework, no hidden state.
 */

const $ = (id) => document.getElementById(id);

let lastState = null;         // the most recent /api/state payload
let renderedEventCount = 0;   // so we only append NEW log lines
let brainFile = "SYSTEM_PROMPT.md";
let brainEditing = false;     // while true, polling must not overwrite the editor

// Hierarchical task name: "Task 1", subtasks "1.1", "1.2", ... The label
// is assigned by the server when the task is created; old entries
// without one fall back to their id.
const tlabel = (t) => (t && (t.label || String(t.id))) || "?";
// "1.2" verifies task "1": the parent's label is everything but the last segment.
const parentLabel = (t) => tlabel(t).split(".").slice(0, -1).join(".") || "?";

// Skip DOM updates when nothing changed — otherwise every poll would
// reset text selections and scroll positions in the panels.
const lastHTML = {};
function setHTML(el, html) {
  if (lastHTML[el.id] !== html) {
    lastHTML[el.id] = html;
    el.innerHTML = html;
  }
}

// ---------------------------------------------------------------------------
// Polling loop
// ---------------------------------------------------------------------------

async function poll() {
  try {
    const state = await (await fetch("/api/state")).json();
    lastState = state;
    renderHeader(state);
    renderLoop(state);
    renderTask(state.status, state.tasks);
    renderTools(state.tools);
    renderScreen(state.screen);
    renderTimeline(state.events);
    renderEvents(state.events, state.events_offset || 0,
                 state.events_total ?? state.events.length);
    renderCalls(state.llm_calls, state.events);
    renderMemories(state.memories);
    renderWorkspace(state.workspace);
    renderTasks(state.tasks);
  } catch (e) {
    // Server not reachable (restarting?) — just try again next tick.
  }
}
setInterval(poll, 1000);
poll();

// The brain files change rarely — refresh them every 5 seconds.
async function pollBrain() {
  try {
    const files = await (await fetch("/api/files")).json();
    window.brainFiles = files;
    if (!brainEditing) {
      const text = files[brainFile] || "";
      if ($("brain-content").textContent !== text) {
        $("brain-content").textContent = text;
      }
    }
  } catch (e) { /* retry next tick */ }
}
setInterval(pollBrain, 5000);
pollBrain();

// ---------------------------------------------------------------------------
// Render functions — each one fills exactly one panel
// ---------------------------------------------------------------------------

function renderHeader(state) {
  renderModelPicker(state.config);
  $("endpoint").textContent = state.config.endpoint || "(not set)";
  $("apikey").textContent = state.config.api_key_masked;

  const s = state.status.status; // idle | running | paused | done | failed | stopped
  const badge = $("status-badge");
  // While a verification or planning task runs, say so instead of RUNNING.
  const verifying = (s === "running" && state.status.kind === "verification");
  const planning = (s === "running" && state.status.planning);
  badge.textContent = verifying ? "VERIFYING" : planning ? "PLANNING" : s.toUpperCase();
  badge.className = "badge " + (verifying ? "verifying" : planning ? "planning" : s);
  const maxIter = (s === "running" || s === "paused")
    ? state.status.max_iterations : state.settings.max_iterations;
  $("iteration").textContent = `${state.status.iteration}/${maxIter}`;

  // Token totals, summed from the llm_call_done events (the API reports
  // them in its "usage" field). Watch the "read" number grow every
  // iteration — that is the context getting longer.
  let tin = 0, tout = 0;
  for (const e of state.events) {
    if (e.type === "llm_call_done") { tin += e.tokens_in || 0; tout += e.tokens_out || 0; }
  }
  $("tokens").textContent = `${fmtTokens(tin)} read / ${fmtTokens(tout)} written`;

  const busy = (s === "running" || s === "paused");
  // Submitting while busy is fine now — the task just joins the queue.
  $("run-btn").textContent = busy ? "＋ Queue task" : "▶ Run task";
  $("reset-btn").disabled = busy;
  $("reset-all-btn").disabled = busy;
  $("stop-btn").hidden = !busy;
  $("step-btn").hidden = (s !== "paused");

  // Sync the controls with the files on the server — but never fight
  // the user while they are mid-click / mid-typing.
  const check = $("step-mode-check");
  if (document.activeElement !== check && check.checked !== state.step_mode) {
    check.checked = state.step_mode;
  }
  const verify = $("verify-check");
  if (document.activeElement !== verify && verify.checked !== state.settings.verify) {
    verify.checked = state.settings.verify;
  }
  const plan = $("plan-check");
  if (document.activeElement !== plan && plan.checked !== state.settings.plan) {
    plan.checked = state.settings.plan;
  }
  const maxIn = $("max-iter-input");
  if (document.activeElement !== maxIn
      && Number(maxIn.value) !== state.settings.max_iterations) {
    maxIn.value = state.settings.max_iterations;
  }
}

function fmtTokens(n) {
  return n >= 10000 ? (n / 1000).toFixed(1) + "k" : String(n);
}

// The model picker: with one model in .env it is a plain label, with
// several it becomes a dropdown. The choice is saved to settings.json
// (via /api/settings) and the very next LLM call reads it — even mid-task.
function renderModelPicker(config) {
  const models = config.models || [];
  const sel = $("model-select");
  const single = models.length < 2;
  $("model").hidden = !single;
  sel.hidden = single;
  if (single) {
    $("model").textContent = config.model || "(not set)";
    return;
  }
  const key = JSON.stringify(models);
  if (lastHTML["model-select"] !== key) {   // rebuild only when the list changes
    lastHTML["model-select"] = key;
    sel.innerHTML = "";
    for (const m of models) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.label;
      opt.title = `${m.model} — ${m.endpoint}`;
      sel.appendChild(opt);
    }
  }
  if (document.activeElement !== sel && sel.value !== String(config.selected)) {
    sel.value = config.selected;
  }
}

// ---------------------------------------------------------------------------
// The loop diagram: light up the step the agent is in RIGHT NOW.
// We don't need any extra state for this — the last event tells us.
// ---------------------------------------------------------------------------

const LOOP_PHASE = {   // last event type -> [step element, caption]
  iteration_start: ["ls-context", "Pasting the brain files, tools, memories and the conversation into one big prompt…"],
  paused:          ["ls-context", "Paused before the next round — press 'Next step' to continue."],
  llm_call_start:  ["ls-ask",     "The whole prompt is on its way to the model — waiting for the answer…"],
  llm_call_error:  ["ls-ask",     "The model could not be reached — retrying…"],
  llm_call_done:   ["ls-decide",  "Answer received — looking for the JSON tool call at the end of the reply…"],
  protocol_error:  ["ls-decide",  "The reply broke the one-tool-call rule — the model is told what happened and continues."],
  tool_call:       ["ls-tool",    "The orchestrator is executing the tool the model chose…"],
  tool_result:     ["ls-tool",    "Tool finished — its result is appended to the conversation, then the loop repeats."],
  task_done:       ["ls-finish",  "The model called 'finish' — the loop has ended."],
};
// task_queued is logged for user tasks, subtasks and verifications alike —
// the caption depends on which kind it was.
const QUEUED_PHASE = {
  verification: ["ls-finish", "A verification task was queued — a fresh run will now check this work."],
  subtask:      ["ls-tool",   "The model queued a subtask — it will run as its own fresh agent run later."],
  user:         [null,        "A new task joined the queue — it starts as soon as the agent is free."],
};

function renderLoop(state) {
  const last = state.events[state.events.length - 1];
  const s = state.status.status;
  const busy = (s === "running" || s === "paused");
  const phase = last && (last.type === "task_queued"
    ? QUEUED_PHASE[last.kind] || QUEUED_PHASE.user
    : LOOP_PHASE[last.type]);
  const idle = 'Steps 1–4 repeat until the model calls the "finish" tool. '
             + "Give the agent a task to watch it live.";
  const [active, caption] = (busy || s === "done") && phase ? phase : [null, idle];
  for (const id of ["ls-context", "ls-ask", "ls-decide", "ls-tool", "ls-finish"]) {
    $(id).classList.toggle("active", id === active);
  }
  const cap = $("loop-caption");
  if (cap.textContent !== caption) cap.textContent = caption;

  // The outer loop: which task is this, and how many are waiting?
  const waiting = state.tasks.filter(t => t.status === "queued").length;
  let info = "";
  if (busy) {
    info = `running task ${state.status.label || state.status.task_id}${
      state.status.kind !== "user" ? " · " + state.status.kind : ""}${
      waiting ? ` · ${waiting} waiting in the queue` : ""}`;
  } else if (waiting) {
    info = `${waiting} task(s) waiting in the queue`;
  }
  const q = $("loop-queue");
  if (q.textContent !== info) q.textContent = info;
}

function renderTask(status, tasks) {
  // The outcome bar: a state chip, the RESULT as the main line, and the
  // task text as muted context underneath.
  const s = status.status;
  const box = $("task-status");
  box.hidden = !status.task;   // nothing has ever run
  if (!status.task) return;

  // Verification tasks carry a long generated prompt — show a short,
  // human label instead (the full text is one click away in Tasks).
  let label = status.task || "";
  if (status.kind === "verification") {
    label = `Verifying the work of task ${parentLabel(status)}`;
  } else if (label.length > 260) {
    label = label.slice(0, 260) + "…";
  }

  let state = s.toUpperCase();
  if (s === "running" && status.kind === "verification") state = "VERIFYING";
  else if (s === "running" && status.planning) state = "PLANNING";
  $("ts-state").textContent = state;
  box.className = "task-status ts-" + s;

  const result = $("task-result");
  if (s === "done")         result.textContent = status.result || "done";
  else if (s === "failed")  result.textContent = status.error || "failed";
  else if (s === "stopped") result.textContent = "Stopped by you.";
  else if (s === "paused")  result.textContent = "Paused — press 'Next step' for the next round.";
  else if (s === "running") result.textContent = `Working — round ${status.iteration || 1}…`;
  else                      result.textContent = "";

  $("task-current").textContent = `task ${status.label || ""}: ${label}`;
}

function renderTools(tools) {
  setHTML($("tools-list"), tools.map(t => `
    <div class="tool">
      <div class="tool-name">${esc(t.name)}</div>
      <div class="tool-desc">${esc(t.description)}</div>
      <div class="tool-args">${
        Object.entries(t.args).map(([a, d]) => `<b>${esc(a)}</b>: ${esc(d)}`).join("<br>")
        || "no arguments"
      }</div>
    </div>`).join(""));
}

function renderScreen(screen) {
  // The screen is plain-text only (no emojis) — so this panel mirrors it
  // with a color-coded text badge instead of an emoji face.
  const mood = screen && ["happy", "neutral", "sad"].includes(screen.mood)
    ? screen.mood : "neutral";
  const badge = $("screen-mood");
  badge.textContent = mood.toUpperCase();
  badge.className = "screen-mood mood-" + mood;
  $("screen-msg").textContent = screen ? screen.message : "(no message yet)";
}

// ---------------------------------------------------------------------------
// Timeline: the whole run compressed to one chip per step. Reads the same
// events as the log below — just the headlines, none of the detail.
// ---------------------------------------------------------------------------

function renderTimeline(events) {
  const chips = [];
  const chip = (cls, label, title, callId) => chips.push(
    `<span class="tl-chip tl-${cls}"${title ? ` title="${esc(title)}"` : ""}${
      callId ? ` onclick="openCall('${String(callId).padStart(3, "0")}')"` : ""
    }>${esc(label)}</span>`);

  for (const e of events) {
    if (e.type === "task_start") {
      const what = e.kind === "verification" ? "verify" : e.planning ? "plan" : "task";
      const name = e.label || (e.task_id != null ? "#" + e.task_id : "");
      chip("task", `⚑ ${what} ${name}`.trim(), e.task || "");
    } else if (e.type === "llm_call_done") {
      const tokens = e.tokens_in != null
        ? `${e.tokens_in} tokens in, ${e.tokens_out} out — ` : "";
      const model = e.model ? `${e.model} — ` : "";
      chip("llm", `LLM #${e.call_id}`, model + tokens + "click to open", e.call_id);
    } else if (e.type === "llm_call_error") {
      chip("err", "LLM ✗", e.error || "");
    } else if (e.type === "tool_result") {
      const failed = String(e.result || "").startsWith("TOOL ERROR");
      chip(failed ? "err" : "tool", (failed ? "✗ " : "") + e.tool,
           (e.result || "").slice(0, 160));
    } else if (e.type === "protocol_error") {
      chip("err", "✗ protocol", "The reply broke the one-tool-call rule.");
    } else if (e.type === "planning_incomplete") {
      chip("err", "✗ empty plan", "Tried to finish planning without subtasks.");
    } else if (e.type === "task_done") {
      chip("done", "✓ done", e.summary || "");
    } else if (e.type === "task_failed") {
      chip("err", "✗ failed", e.error || "");
    } else if (e.type === "task_stopped") {
      chip("stop", "⏹ stopped", "");
    }
  }
  setHTML($("timeline"), chips.join("") ||
    `<div class="empty">Every step will appear here as a small chip —
     tasks, LLM calls, tools — the whole run at a glance.</div>`);
  const tl = $("timeline");
  if (chips.length) tl.scrollTop = tl.scrollHeight;
}

// The log is a plain-English STORY. Technical details exist under every
// entry but stay hidden until you click the entry (or flip the global
// "show technical details" switch). Pairs of events are merged into one
// line: "asking the model…" updates in place when the answer arrives,
// and a tool call + its result become a single "tool → result" entry.
let pendingLLM = {};    // call_id -> the "asking the model…" node
let pendingTool = null; // the "running tool…" node (tools run one at a time)

function techLine(e) {
  const details = Object.entries(e)
    .filter(([k]) => k !== "time" && k !== "type" && k !== "note")
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join("  ");
  return `<div class="ev-tech"><span class="t">${esc(e.time.slice(11))}</span> ` +
         `<span class="type">${esc(e.type)}</span> ${esc(details)}</div>`;
}

function fillEvent(div, type, headlineHtml, e, extra = "") {
  div.className = `event new ${type}`;
  div.innerHTML = `<div class="ev-note">${headlineHtml}${extra}</div>` + techLine(e);
}

function addEvent(log, e) {
  // Task and round boundaries render as dividers, not entries.
  if (e.type === "task_start") {
    const div = document.createElement("div");
    div.className = "task-divider new";
    const title = `⚑ TASK ${esc(e.label || "")}: ${esc((e.task || "").slice(0, 160))}`;
    div.innerHTML =
      `<div class="td-title">${title}</div>` +
      `<div class="ev-tech"><span class="t">${esc(e.time.slice(11))}</span> ` +
      `${esc(e.note || "")}</div>`;
    log.appendChild(div);
    return;
  }
  if (e.type === "iteration_start") {
    const div = document.createElement("div");
    div.className = "round-divider new";
    div.textContent = `· round ${e.iteration} ·`;
    log.appendChild(div);
    return;
  }

  if (e.type === "llm_call_start") {
    const div = document.createElement("div");
    fillEvent(div, "llm_call_start", `Asking the model…`, e);
    pendingLLM[e.call_id] = div;
    log.appendChild(div);
    return;
  }
  if (e.type === "llm_call_done" || e.type === "llm_call_error") {
    const div = pendingLLM[e.call_id] || document.createElement("div");
    const open = `<span class="ev-open">call #${e.call_id} ↗</span>`;
    fillEvent(div, e.type, esc(e.note || ""), e, e.type === "llm_call_done" ? open : "");
    if (e.type === "llm_call_done") {
      div.title = "Click to open the full request and response";
      div.onclick = () => openCall(String(e.call_id).padStart(3, "0"));
      delete pendingLLM[e.call_id];
    }
    if (!div.parentNode) log.appendChild(div);
    return;
  }

  if (e.type === "tool_call") {
    const div = document.createElement("div");
    fillEvent(div, "tool_call", `Running <b>${esc(e.tool)}</b>…`, e);
    pendingTool = div;
    log.appendChild(div);
    return;
  }
  if (e.type === "tool_result") {
    const div = pendingTool || document.createElement("div");
    pendingTool = null;
    const r = String(e.result || "");
    const failed = r.startsWith("TOOL ERROR");
    const snippet = (failed ? r.slice(11) : r).trim().slice(0, 150);
    fillEvent(div, failed ? "tool_error" : "tool_result",
      failed ? `<b>${esc(e.tool)}</b> refused —${esc(snippet)}`
             : `<b>${esc(e.tool)}</b> → ${esc(snippet)}`, e);
    div.title = "Click for the technical details";
    div.onclick = () => div.classList.toggle("show-tech");
    if (!div.parentNode) log.appendChild(div);
    return;
  }

  // Everything else: one plain-English line (details on click).
  const div = document.createElement("div");
  fillEvent(div, e.type, esc(e.note || e.type), e);
  div.title = "Click for the technical details";
  div.onclick = () => div.classList.toggle("show-tech");
  log.appendChild(div);
}

function renderEvents(events, offset, total) {
  const log = $("event-log");

  // A reset shrank the log — start over. (The server sends only a recent
  // window of events; `offset` + `total` keep our place in the full log.)
  if (total < renderedEventCount) {
    log.innerHTML = "";
    renderedEventCount = 0;
    pendingLLM = {}; pendingTool = null;
  }

  // Nothing happened yet: explain what this panel will show.
  if (!total) {
    if (!log.querySelector(".empty")) {
      log.innerHTML = `<div class="empty">Nothing yet. Give the agent a task
        and the story of the run will appear here, step by step.</div>`;
    }
    return;
  }
  if (renderedEventCount === 0) {
    log.innerHTML = ""; // remove the placeholder
    pendingLLM = {}; pendingTool = null;
  }
  if (renderedEventCount < offset) renderedEventCount = offset;

  for (let i = renderedEventCount - offset; i < events.length; i++) {
    addEvent(log, events[i]);
  }
  if (total > renderedEventCount) {
    log.scrollTop = log.scrollHeight;   // auto-scroll to the newest line
  }
  renderedEventCount = total;
}

// The global switch: reveal every technical line at once. Great for the
// second half of a workshop — first tell the story, then show the wiring.
{
  const toggle = $("tech-toggle");
  let on = false;
  try { on = localStorage.getItem("show-tech") === "1"; } catch (err) {}
  toggle.checked = on;
  $("event-log").classList.toggle("show-tech", on);
  toggle.onchange = () => {
    $("event-log").classList.toggle("show-tech", toggle.checked);
    try { localStorage.setItem("show-tech", toggle.checked ? "1" : "0"); } catch (err) {}
  };
}

function renderCalls(calls, events) {
  const box = $("calls-list");
  if (!calls.length) {
    setHTML(box, `<div class="empty">No calls yet. Each request to the model
      will be listed here — the full prompt and the full answer, nothing
      hidden.</div>`);
    return;
  }
  // Token counts, task attribution and loop round per call all come from
  // the event log. tokens_in doubles as the CONTEXT BAR below each call:
  // the whole conversation is resent every round, so the bars form a
  // growing staircase — that staircase is the lesson about context.
  const tokens = {}, tokensIn = {}, callTask = {}, callRound = {};
  for (const e of events) {
    if (e.type === "llm_call_done" && e.tokens_in != null) {
      const id = String(e.call_id).padStart(3, "0");
      tokens[id] = `${fmtTokens(e.tokens_in)}→${fmtTokens(e.tokens_out)} tok`;
      tokensIn[id] = e.tokens_in;
    }
    if (e.type === "llm_call_start") {
      const id = String(e.call_id).padStart(3, "0");
      if (e.task_id != null) callTask[id] = e.task_id;
      if (e.iteration != null) callRound[id] = e.iteration;
    }
  }
  const maxIn = Math.max(1, ...Object.values(tokensIn));
  // Build with DOM APIs (not onclick strings) so no name can inject code.
  const key = JSON.stringify([calls, tokens, callTask, callRound]);
  if (lastHTML["calls-list"] === key) return;
  lastHTML["calls-list"] = key;
  box.innerHTML = "";
  const labelById = {};
  for (const t of (lastState?.tasks || [])) labelById[t.id] = tlabel(t);
  for (const c of calls) {
    const failed = c.id.includes("_error");
    const base = c.id.slice(0, 3);
    const t = callTask[base];
    const forTask = t != null ? ` · task ${labelById[t] ?? t}` : "";
    const forRound = callRound[base] != null ? ` · round ${callRound[base]}` : "";
    const div = document.createElement("div");
    div.className = "call-item" + (failed ? " failed" : "");
    const row = document.createElement("div");
    row.className = "call-row";
    const label = document.createElement("span");
    label.textContent = failed
      ? `▸ call #${base} (failed attempt)${forTask}${forRound}`
      : `▸ call #${c.id}${forTask}${forRound}`;
    const meta = document.createElement("span");
    meta.className = "sz";
    meta.textContent = [tokens[c.id], (c.size / 1024).toFixed(1) + " kB"]
      .filter(Boolean).join(" · ");
    row.append(label, meta);
    div.append(row);
    if (tokensIn[c.id] != null) {
      const bar = document.createElement("div");
      bar.className = "call-bar";
      bar.title = `${tokensIn[c.id].toLocaleString()} tokens of context went into this call `
                + `(the longest call in this list = full width)`;
      const fill = document.createElement("span");
      fill.style.width = Math.max(1, (tokensIn[c.id] / maxIn) * 100) + "%";
      bar.appendChild(fill);
      div.append(bar);
    }
    div.addEventListener("click", () => openCall(c.id));
    box.appendChild(div);
  }
}

function renderMemories(memories) {
  const names = Object.keys(memories);
  if (!names.length) {
    setHTML($("memory-list"), `<div class="empty">Empty. When the agent uses
      its save_memory tool, one small text file appears here — and gets
      pasted into every future prompt.</div>`);
    return;
  }
  setHTML($("memory-list"), names.map(name => `
    <div class="memory-card">
      <div class="mem-name">${esc(name)}</div>
      <pre>${esc(memories[name])}</pre>
    </div>`).join(""));
}

function renderWorkspace(files) {
  const box = $("workspace-list");
  if (!files.length) {
    setHTML(box, `<div class="empty">Empty. Ask the agent to create a file
      (e.g. "write a poem into poem.txt") and it will show up here.</div>`);
    return;
  }
  const key = JSON.stringify(files);
  if (lastHTML["workspace-list"] === key) return;
  lastHTML["workspace-list"] = key;
  box.innerHTML = "";
  for (const f of files) {
    const div = document.createElement("div");
    div.className = "ws-item";
    const label = document.createElement("span");
    label.textContent = "▸ " + f.name;
    const size = document.createElement("span");
    size.className = "sz";
    size.textContent = (f.size / 1024).toFixed(1) + " kB";
    div.append(label, size);
    div.addEventListener("click", () => openWorkspaceFile(f.name));
    box.appendChild(div);
  }
}

function renderTasks(tasks) {
  if (!tasks.length) {
    setHTML($("tasks-list"), `<div class="empty">No tasks yet. Submitted
      tasks line up here and run one at a time — with self-verification
      on, each finished task queues its own verification task.</div>`);
    return;
  }
  // Group by family: newest user task first, then its children (subtasks,
  // verifications — recursively) indented beneath it, oldest first.
  const children = (id) =>
    tasks.filter(c => c.parent === id).flatMap(c => [c, ...children(c.id)]);
  const roots = tasks.filter(t => t.parent == null).reverse();
  const ordered = roots.flatMap(r => [r, ...children(r.id)]);
  // Safety net: show orphans (parent missing) rather than losing them.
  for (const t of tasks) if (!ordered.includes(t)) ordered.push(t);

  const icons = { queued: "⏳", running: "▶", done: "✔", failed: "✘", stopped: "⏹" };
  setHTML($("tasks-list"), ordered.map(t => `
    <div class="history-item ${esc(t.status)} ${esc(t.kind)}"
         onclick="openTask(${Number(t.id)})" title="click for details">
      <div class="hist-head">
        <span class="hist-icon">${icons[t.status] || "•"}</span>
        <span class="hist-task">${
          t.kind === "verification"
            ? `↳ ${esc(tlabel(t))} · verification of ${esc(parentLabel(t))}`
          : t.kind === "subtask"
            ? `↳ ${esc(tlabel(t))} · ${esc(t.task || "")}`
            : `Task ${esc(tlabel(t))} · ${esc(t.task || "")}`}</span>
        ${t.status === "running" ? `<span class="hist-now">NOW</span>` : ""}
        <span class="hist-time">${esc((t.created || "").slice(11, 16))}</span>
      </div>
      <div class="hist-result">${esc(t.result || t.error ||
          (t.status === "queued" ? "waiting in the queue…" : ""))}
        ${t.iterations ? `<span class="hist-iters">· ${t.iterations} rounds</span>` : ""}</div>
    </div>`).join(""));
}

// Click a task row: show its full description, outcome and STORY — the
// run retold round by round, subtasks and verifications included.
async function openTask(id) {
  const t = (lastState?.tasks || []).find(x => x.id === id);
  if (!t) return;
  $("call-modal-title").textContent =
    `Task ${tlabel(t)} · ${t.kind} · ${t.status}`;
  $("call-modal-raw").style.display = "none";
  const box = $("call-modal-content");
  box.innerHTML = "";
  const mk = (label, text) => {
    if (!text) return;
    const h = document.createElement("div");
    h.className = "call-section";
    h.textContent = label;
    const pre = document.createElement("pre");
    pre.className = "raw";
    pre.textContent = text;
    box.append(h, pre);
  };
  mk("TASK DESCRIPTION — what this run was told to do", t.task);
  mk("OUTCOME", t.result || t.error ||
     (t.status === "queued" ? "Still waiting in the queue." : "Still running."));

  // The story is assembled server-side from the event log — the same
  // text is in agent_data/logs/narratives.md for every top-level task.
  const h = document.createElement("div");
  h.className = "call-section";
  h.textContent = "THE STORY — what happened, round by round";
  const hint = document.createElement("div");
  hint.className = "call-hint";
  hint.innerHTML = `Retold from <b>events.jsonl</b>: what the model thought,
    which tool it called, what came back — and the same for every subtask
    and verification this task spawned. No model wrote this; it is the log.`;
  const story = document.createElement("div");
  story.className = "story";
  story.innerHTML = `<div class="empty">Loading…</div>`;
  box.append(h, hint, story);
  $("call-modal").classList.remove("hidden");

  try {
    const data = await (await fetch(`/api/narrative/${Number(id)}`)).json();
    story.innerHTML = renderStory(data.markdown || "");
  } catch (err) {
    story.innerHTML = `<div class="empty">Could not load the story.</div>`;
  }
}

// A tiny markdown renderer — just enough for narrative.py's output:
// "## " / "### " headings, "**bold**", and "- " bullets nested by
// indentation. Everything is escaped first, so log content can't inject.
function renderStory(md) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  let html = "", depth = 0;
  const closeTo = (d) => { while (depth > d) { html += "</ul>"; depth--; } };
  for (const raw of md.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (!line.trim()) continue;
    const lead = line.match(/^ */)[0].length;
    const body = line.trim();
    let m;
    if ((m = body.match(/^(#{2,3}) (.*)$/))) {
      closeTo(0);
      html += `<div class="story-h${m[1].length}">${inline(m[2])}</div>`;
    } else if ((m = body.match(/^- (.*)$/))) {
      // A bullet at indentation N (in steps of 2 spaces) is nested N deep.
      const d = Math.floor(lead / 2) + 1;
      while (depth < d) { html += "<ul>"; depth++; }
      closeTo(d);
      html += `<li>${inline(m[1])}</li>`;
    } else {
      closeTo(0);
      html += `<p>${inline(body)}</p>`;
    }
  }
  closeTo(0);
  return html || `<div class="empty">Nothing to tell yet.</div>`;
}

// ---------------------------------------------------------------------------
// Interactions: run / stop / step / reset
// ---------------------------------------------------------------------------

async function submitTask() {
  const task = $("task-input").value.trim();
  if (!task) return;
  const res = await fetch("/api/task", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task }),
  });
  if (!res.ok) alert((await res.json()).error);
  poll(); // update immediately instead of waiting for the next tick
}
$("run-btn").onclick = submitTask;
$("task-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submitTask();
});

$("stop-btn").onclick = () => fetch("/api/stop", { method: "POST" }).then(poll);
$("step-btn").onclick = () => fetch("/api/continue", { method: "POST" }).then(poll);

// A shortcut for the stage: while step mode has the agent paused, Space
// (or N) presses "Next step" — so you can narrate without mousing around.
document.addEventListener("keydown", (e) => {
  if (e.key !== " " && e.key.toLowerCase() !== "n") return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if ($("step-btn").hidden) return;   // only while actually paused
  const t = e.target;
  if (t.matches("input, textarea, select") || t.isContentEditable) return;
  e.preventDefault();                 // Space must not scroll the page here
  $("step-btn").click();
});

$("step-mode-check").onchange = (e) =>
  fetch("/api/step_mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ on: e.target.checked }),
  }).then(poll);

async function saveSettings(changes) {
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  if (!res.ok) alert((await res.json()).error);
  poll();
}
$("verify-check").onchange = (e) => saveSettings({ verify: e.target.checked });
$("plan-check").onchange = (e) => saveSettings({ plan: e.target.checked });
$("max-iter-input").onchange = (e) => saveSettings({ max_iterations: Number(e.target.value) });
// The model is a setting like any other — the pick lands in settings.json.
$("model-select").onchange = (e) => saveSettings({ model: e.target.value });

$("reset-btn").onclick = async () => {
  const res = await fetch("/api/reset", { method: "POST" });
  if (!res.ok) alert((await res.json()).error);
  poll();
};

$("reset-all-btn").onclick = async () => {
  if (!confirm("Reset EVERYTHING? Logs, LLM calls, memories, workspace files "
             + "and the brain files all go back to a fresh install. "
             + "Only .env is kept.")) return;
  const res = await fetch("/api/reset_all", { method: "POST" });
  if (!res.ok) alert((await res.json()).error);
  poll();
  pollBrain(); // brain files changed back to their seeds
};

// ---------------------------------------------------------------------------
// LLM call viewer: renders the request's messages as chat-style cards
// (system / user / assistant) plus the model's reply. "show raw file"
// toggles the untouched .txt from disk.
// ---------------------------------------------------------------------------

let currentCall = null;   // the last fetched call, for the raw/pretty toggle
let showRaw = false;

async function openCall(id) {
  currentCall = await (await fetch(`/api/llm_calls/${encodeURIComponent(id)}`)).json();
  showRaw = false;
  $("call-modal-title").textContent = `LLM call #${id}`;
  $("call-modal-raw").style.display = "";
  renderCallModal();
  $("call-modal").classList.remove("hidden");
}

// The same modal doubles as a simple file viewer for workspace files.
async function openWorkspaceFile(name) {
  const text = await (await fetch(`/api/workspace/${encodeURIComponent(name)}`)).text();
  $("call-modal-title").textContent = `workspace/${name}`;
  $("call-modal-raw").style.display = "none";
  $("call-modal-content").innerHTML = "";
  const pre = document.createElement("pre");
  pre.className = "raw";
  pre.textContent = text;
  $("call-modal-content").appendChild(pre);
  $("call-modal").classList.remove("hidden");
}

function renderCallModal() {
  $("call-modal-raw").textContent = showRaw ? "show formatted" : "show raw file";
  const box = $("call-modal-content");

  // Fallback (or on request): the plaintext file exactly as on disk.
  if (showRaw || !currentCall.request) {
    box.innerHTML = `<pre class="raw">${esc(currentCall.raw)}</pre>`;
    return;
  }

  const req = currentCall.request;
  const usage = (currentCall.response && currentCall.response.usage) || null;
  const usageText = usage
    ? `&nbsp;·&nbsp; ${usage.prompt_tokens} tokens read, ${usage.completion_tokens} written` : "";
  const endpoint = currentCall.url
    ? ` &nbsp;·&nbsp; sent to: <b>${esc(currentCall.url)}</b>` : "";
  let html = `<div class="call-meta">model: <b>${esc(req.model)}</b>${endpoint}
              &nbsp;·&nbsp; ${req.messages.length} messages sent
              &nbsp;·&nbsp; response: ${esc(currentCall.status || "?")}${usageText}</div>`;

  html += `<div class="call-section">REQUEST — what the model saw</div>`;
  html += `<div class="call-hint">The <b>system</b> message is the assembled
    system prompt, rebuilt fresh for this call — each labeled block below is
    one brain file (or the tool list / the memories) pasted in. In the real
    request they are one long text (see "show raw file"). Everything after
    it is the conversation so far, including earlier tool results.</div>`;
  for (const m of req.messages) {
    html += m.role === "system" ? systemCard(m.content) : msgCard(m.role, m.content);
  }

  html += `<div class="call-section">RESPONSE — what the model answered</div>`;
  html += `<div class="call-hint">The model can only answer with text. The
    JSON block at the end of this reply is what the orchestrator parses
    and turns into a real action.</div>`;
  const resp = currentCall.response;
  if (resp && resp.choices) {
    html += msgCard("assistant", resp.choices[0].message.content);
  } else {
    html += msgCard("error", typeof resp === "string" ? resp : JSON.stringify(resp, null, 2));
  }
  box.innerHTML = html;
}

function msgCard(role, content) {
  return `<div class="msg-card role-${esc(role)}">
            <div class="msg-role">${esc(role)}</div>
            <pre class="msg-content">${esc(content)}</pre>
          </div>`;
}

// The system message is build_system_prompt()'s parts joined with "---".
// Split it back apart and label each part, so you can SEE that the prompt
// is just the brain files, the tool list and the memories concatenated.
const SYS_PARTS = [
  ["SYSTEM_PROMPT.md", "the rules of the game"],
  ["tool list", "generated from the registry in tools.py"],
  ["GHOST.md", "the persona"],
  ["KNOWLEDGE.md", "injected facts"],
  ["memory/*.md", "every saved memory"],
];

function systemCard(content) {
  const parts = String(content).split("\n\n---\n\n");
  // Label only what we can identify with certainty — a brain file that
  // itself contains a "---" line would shift the labels, so fall back to
  // the plain one-block card rather than mislabel anything.
  const looksRight = (parts.length === 4 || parts.length === 5)
    && parts[1].startsWith("## Available tools")
    && (parts.length === 4 || parts[4].startsWith("## Your memories"));
  if (!looksRight) return msgCard("system", content);
  const segs = parts.map((p, i) => `
    <div class="sys-seg sys-seg-${i}">
      <div class="sys-seg-label">${esc(SYS_PARTS[i][0])} <span>— ${esc(SYS_PARTS[i][1])}</span></div>
      <pre class="msg-content">${esc(p)}</pre>
    </div>`).join("");
  return `<div class="msg-card role-system">
            <div class="msg-role">system — one prompt, assembled from ${parts.length} parts</div>
            ${segs}
          </div>`;
}

$("call-modal-raw").onclick = () => { showRaw = !showRaw; renderCallModal(); };
$("call-modal-close").onclick = () => $("call-modal").classList.add("hidden");
$("call-modal").onclick = (e) => {
  if (e.target === $("call-modal")) $("call-modal").classList.add("hidden");
};

// "How it works" — the intro modal for workshop participants.
$("help-btn").onclick = () => $("help-modal").classList.remove("hidden");
$("help-modal-close").onclick = () => $("help-modal").classList.add("hidden");
$("help-modal").onclick = (e) => {
  if (e.target === $("help-modal")) $("help-modal").classList.add("hidden");
};

// ---------------------------------------------------------------------------
// Brain files: tabs + in-dashboard editing.
// Saving POSTs the text; the agent re-reads the files before every LLM
// call, so a save applies to the very next iteration — even mid-task.
// ---------------------------------------------------------------------------

document.querySelectorAll(".tab").forEach(tab => {
  tab.onclick = () => {
    if (brainEditing) return; // finish or cancel the edit first
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    brainFile = tab.dataset.file;
    $("brain-content").textContent = (window.brainFiles || {})[brainFile] || "…";
  };
});

function setBrainEditing(on) {
  brainEditing = on;
  $("brain-content").hidden = on;
  $("brain-editor").hidden = !on;
  $("brain-edit").hidden = on;
  $("brain-save").hidden = !on;
  $("brain-cancel").hidden = !on;
  document.querySelectorAll(".tab").forEach(t => t.disabled = on);
}

$("brain-edit").onclick = () => {
  $("brain-editor").value = (window.brainFiles || {})[brainFile] || "";
  setBrainEditing(true);
  $("brain-editor").focus();
};

$("brain-cancel").onclick = () => setBrainEditing(false);

$("brain-save").onclick = async () => {
  const content = $("brain-editor").value;
  const res = await fetch("/api/brain", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: brainFile, content }),
  });
  if (!res.ok) { alert((await res.json()).error); return; }
  window.brainFiles[brainFile] = content;
  $("brain-content").textContent = content;
  setBrainEditing(false);
};

// ---------------------------------------------------------------------------
// Collapsible panels: click any panel header to fold it away. The choice
// is remembered in this browser (localStorage) — it never reaches the
// server, matching the rule that the dashboard is only a viewer.
// ---------------------------------------------------------------------------

function loadCollapsed() {
  try { return JSON.parse(localStorage.getItem("collapsed-panels")) || []; }
  catch (e) { return []; }
}
function saveCollapsed() {
  const ids = [...document.querySelectorAll("section.panel.collapsed")].map(p => p.id);
  try { localStorage.setItem("collapsed-panels", JSON.stringify(ids)); }
  catch (e) { /* private mode etc. — collapsing just won't persist */ }
}
{
  const collapsed = loadCollapsed();
  document.querySelectorAll("section.panel").forEach(p => {
    const h2 = p.querySelector("h2");
    if (!h2) return;
    const icon = document.createElement("span");
    icon.className = "collapse-icon";
    h2.prepend(icon);
    if (collapsed.includes(p.id)) p.classList.add("collapsed");
    h2.addEventListener("click", () => {
      p.classList.toggle("collapsed");
      saveCollapsed();
    });
  });
}

// Tiny helper: escape HTML so log/file content can't inject markup.
function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
