/*
 * dashboard.js — the whole dashboard in plain JavaScript.
 *
 * The mechanism is deliberately primitive: once per second we fetch
 * /api/state (one GET request — watch it in the browser's Network tab)
 * and re-render every panel from that single JSON payload.
 * The server, in turn, only reads plaintext files. No websockets,
 * no framework, no hidden state.
 *
 * Every sentence the dashboard says comes from i18n.js (English / German)
 * through t(). Three rules keep the HTML escaping straight:
 *   1. el.textContent = t("key")               — plain text, always safe
 *   2. `…${esc(t("key"))}…` inside innerHTML   — the same rule as for all data
 *   3. tHtml("key", params)                    — ONLY for the few keys whose
 *      text contains <b> tags: the params are escaped, the template is not
 */

const $ = (id) => document.getElementById(id);

// Static page text (headings, buttons, tooltips, the help prose) is filled
// in once; the render functions below handle everything that changes.
applyTranslations();

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
  $("endpoint").textContent = state.config.endpoint || t("header.not_set");
  $("apikey").textContent = state.config.api_key_masked || t("header.not_set");

  const s = state.status.status; // idle | running | paused | done | failed | stopped
  const key = statusKey(state.status);
  const badge = $("status-badge");
  badge.textContent = t("status." + key);
  badge.className = "badge " + key;
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
  $("tokens").textContent = t("header.tokens", { in: fmtTokens(tin), out: fmtTokens(tout) });

  const busy = (s === "running" || s === "paused");
  // Submitting while busy is fine now — the task just joins the queue.
  $("run-btn").textContent = t(busy ? "task.queue" : "task.run");
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

// The label of the status badge / chip. While a verification or planning
// task runs, say so instead of RUNNING. The key doubles as the CSS class.
function statusKey(status) {
  const s = status.status;
  if (s === "running" && status.kind === "verification") return "verifying";
  if (s === "running" && status.planning) return "planning";
  return s;
}

function fmtTokens(n) {
  return n >= 10000 ? fmt1(n / 1000) + "k" : String(n);
}
// One decimal, in the dashboard's language: 12.3 (en) / 12,3 (de).
function fmt1(x) {
  return x.toLocaleString(LANG, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
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
    $("model").textContent = config.model || t("header.not_set");
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

const LOOP_PHASE = {   // last event type -> [step element, caption key (see i18n.js)]
  iteration_start: ["ls-context", "loop.phase.iteration_start"],
  paused:          ["ls-context", "loop.phase.paused"],
  llm_call_start:  ["ls-ask",     "loop.phase.llm_call_start"],
  llm_call_error:  ["ls-ask",     "loop.phase.llm_call_error"],
  llm_call_done:   ["ls-decide",  "loop.phase.llm_call_done"],
  protocol_error:  ["ls-decide",  "loop.phase.protocol_error"],
  tool_call:       ["ls-tool",    "loop.phase.tool_call"],
  tool_result:     ["ls-tool",    "loop.phase.tool_result"],
  task_done:       ["ls-finish",  "loop.phase.task_done"],
};
// task_queued is logged for user tasks, subtasks and verifications alike —
// the caption depends on which kind it was.
const QUEUED_PHASE = {
  verification: ["ls-finish", "loop.queued.verification"],
  subtask:      ["ls-tool",   "loop.queued.subtask"],
  user:         [null,        "loop.queued.user"],
};

function renderLoop(state) {
  const last = state.events[state.events.length - 1];
  const s = state.status.status;
  const busy = (s === "running" || s === "paused");
  const phase = last && (last.type === "task_queued"
    ? QUEUED_PHASE[last.kind] || QUEUED_PHASE.user
    : LOOP_PHASE[last.type]);
  const [active, captionKey] = (busy || s === "done") && phase ? phase : [null, null];
  for (const id of ["ls-context", "ls-ask", "ls-decide", "ls-tool", "ls-finish"]) {
    $(id).classList.toggle("active", id === active);
  }
  const cap = $("loop-caption");
  const caption = captionKey ? t(captionKey) : "";   // idle: nothing to narrate
  if (cap.textContent !== caption) cap.textContent = caption;

  // The outer loop: which task is this, and how many are waiting?
  const waiting = state.tasks.filter(x => x.status === "queued").length;
  let info = "";
  if (busy) {
    info = t("loop.running_task", { label: state.status.label || state.status.task_id })
         + (state.status.kind !== "user" ? " · " + t("kind." + state.status.kind) : "")
         + (waiting ? " · " + t("loop.waiting_suffix", { n: waiting }) : "");
  } else if (waiting) {
    info = t("loop.waiting", { n: waiting });
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
    label = t("task.verifying", { label: parentLabel(status) });
  } else if (label.length > 260) {
    label = label.slice(0, 260) + "…";
  }

  $("ts-state").textContent = t("status." + statusKey(status));
  box.className = "task-status ts-" + s;

  const result = $("task-result");
  if (s === "done")         result.textContent = outcomeText(status) || t("task.result_done");
  else if (s === "failed")  result.textContent = outcomeText(status) || t("task.result_failed");
  else if (s === "stopped") result.textContent = t("task.stopped");
  else if (s === "paused")  result.textContent = t("task.paused");
  else if (s === "running") result.textContent = t("task.working", { n: status.iteration || 1 });
  else                      result.textContent = "";

  $("task-current").textContent = t("task.current", { label: status.label || "", text: label });
}

// The outcome of a task (or of status.json): its result, or its error
// sentence. The orchestrator writes that sentence in English and attaches an
// error_code (see storage.py) — a known code is shown in the dashboard's
// language; anything else (an LLM error, a crash) is shown as stored,
// because the message itself is the information.
function outcomeText(x) {
  if (x.result) return x.result;
  const key = "outcome." + x.error_code;
  if (x.error_code && I18N[key]) return t(key, { n: x.iterations ?? x.iteration ?? 0 });
  return x.error || "";
}

function renderTools(tools) {
  // Names, descriptions and arguments are the literal prompt text (see
  // tools.py) — shown as the model reads them, so they are not translated.
  setHTML($("tools-list"), tools.map(tool => `
    <div class="tool">
      <div class="tool-name">${esc(tool.name)}</div>
      <div class="tool-desc">${esc(tool.description)}</div>
      <div class="tool-args">${
        Object.entries(tool.args).map(([a, d]) => `<b>${esc(a)}</b>: ${esc(d)}`).join("<br>")
        || esc(t("tools.no_args"))
      }</div>
    </div>`).join(""));
}

function renderScreen(screen) {
  // The screen is plain-text only (no emojis) — so this panel mirrors it
  // with a color-coded text badge instead of an emoji face.
  const mood = screen && ["happy", "neutral", "sad"].includes(screen.mood)
    ? screen.mood : "neutral";
  const badge = $("screen-mood");
  badge.textContent = t("mood." + mood);
  badge.className = "screen-mood mood-" + mood;
  $("screen-msg").textContent = screen ? screen.message : t("screen.empty");
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
      chip("task", `⚑ ${t("timeline.kind." + what)} ${name}`.trim(), e.task || "");
    } else if (e.type === "llm_call_done") {
      const tip = [
        e.model,
        e.tokens_in != null ? t("timeline.tokens", { in: e.tokens_in, out: e.tokens_out }) : "",
        t("timeline.click_open"),
      ].filter(Boolean).join(" — ");
      chip("llm", `LLM #${e.call_id}`, tip, e.call_id);
    } else if (e.type === "llm_call_error") {
      chip("err", "LLM ✗", e.error || "");
    } else if (e.type === "tool_result") {
      const failed = String(e.result || "").startsWith("TOOL ERROR");
      chip(failed ? "err" : "tool", (failed ? "✗ " : "") + e.tool,
           (e.result || "").slice(0, 160));
    } else if (e.type === "protocol_error") {
      chip("err", "✗ " + t("timeline.protocol"), t("timeline.protocol_tip"));
    } else if (e.type === "planning_incomplete") {
      chip("err", "✗ " + t("timeline.empty_plan"), t("timeline.empty_plan_tip"));
    } else if (e.type === "task_done") {
      chip("done", "✓ " + t("timeline.done"), e.summary || "");
    } else if (e.type === "task_failed") {
      chip("err", "✗ " + t("timeline.failed"), e.error || "");
    } else if (e.type === "task_stopped") {
      chip("stop", "⏹ " + t("timeline.stopped"), "");
    }
  }
  setHTML($("timeline"), chips.join("") ||
    `<div class="empty">${esc(t("timeline.empty"))}</div>`);
  const tl = $("timeline");
  if (chips.length) tl.scrollTop = tl.scrollHeight;
}

// The log is a plain-language STORY. Technical details exist under every
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

// storage.describe_event() writes an English sentence into every line of
// events.jsonl, so the file explains itself. Here the same sentence is
// re-told in the dashboard's language from the event's own fields (the
// templates are the "event.*" entries in i18n.js). Unknown event types
// fall back to the stored note.
function noteFor(e) {
  switch (e.type) {
    case "task_start":
      return t(e.kind === "verification" ? "event.task_start.verification"
             : e.planning ? "event.task_start.planning" : "event.task_start");
    case "planning_incomplete":
      return t("event.planning_incomplete");
    case "task_queued":
      return t(e.kind === "verification" ? "event.task_queued.verification"
             : e.kind === "subtask" ? "event.task_queued.subtask" : "event.task_queued");
    case "iteration_start":
      return t("event.iteration_start", { iteration: e.iteration });
    case "llm_call_start":
      return t("event.llm_call_start",
               { model: e.model || t("event.the_model"), call_id: e.call_id });
    case "llm_call_done":
      return e.tokens_in != null
        ? t("event.llm_call_done.tokens",
            { duration: e.duration, tokens_in: e.tokens_in, tokens_out: e.tokens_out })
        : t("event.llm_call_done", { duration: e.duration });
    case "llm_call_error":
      return t(e.attempt === 1 ? "event.llm_call_error.first" : "event.llm_call_error.again");
    case "tool_call":
      return t("event.tool_call", { tool: e.tool });
    case "tool_result":
      return t(String(e.result || "").startsWith("TOOL ERROR")
               ? "event.tool_result.refused" : "event.tool_result", { tool: e.tool });
    case "protocol_error":
      return (e.count || 0) > 1
        ? t("event.protocol_error.many", { count: e.count })
        : t("event.protocol_error");
    case "task_done": case "task_failed": case "task_stopped": case "paused":
      return t("event." + e.type);
    default:
      return e.note || "";
  }
}

function addEvent(log, e) {
  // Task and round boundaries render as dividers, not entries.
  if (e.type === "task_start") {
    const div = document.createElement("div");
    div.className = "task-divider new";
    const title = t("log.task_divider", { label: e.label || "", text: (e.task || "").slice(0, 160) });
    div.innerHTML =
      `<div class="td-title">${esc(title)}</div>` +
      `<div class="ev-tech"><span class="t">${esc(e.time.slice(11))}</span> ` +
      `${esc(noteFor(e))}</div>`;
    log.appendChild(div);
    return;
  }
  if (e.type === "iteration_start") {
    const div = document.createElement("div");
    div.className = "round-divider new";
    div.textContent = t("log.round", { n: e.iteration });
    log.appendChild(div);
    return;
  }

  if (e.type === "llm_call_start") {
    const div = document.createElement("div");
    fillEvent(div, "llm_call_start", esc(t("log.asking")), e);
    pendingLLM[e.call_id] = div;
    log.appendChild(div);
    return;
  }
  if (e.type === "llm_call_done" || e.type === "llm_call_error") {
    const div = pendingLLM[e.call_id] || document.createElement("div");
    const open = `<span class="ev-open">${esc(t("log.open_call", { id: e.call_id }))}</span>`;
    fillEvent(div, e.type, esc(noteFor(e)), e, e.type === "llm_call_done" ? open : "");
    if (e.type === "llm_call_done") {
      div.title = t("log.click_open");
      div.onclick = () => openCall(String(e.call_id).padStart(3, "0"));
      delete pendingLLM[e.call_id];
    }
    if (!div.parentNode) log.appendChild(div);
    return;
  }

  if (e.type === "tool_call") {
    const div = document.createElement("div");
    fillEvent(div, "tool_call", tHtml("log.tool_running", { tool: e.tool }), e);
    pendingTool = div;
    log.appendChild(div);
    return;
  }
  if (e.type === "tool_result") {
    const div = pendingTool || document.createElement("div");
    pendingTool = null;
    const r = String(e.result || "");
    const failed = r.startsWith("TOOL ERROR");
    const snippet = (failed ? r.slice(10).replace(/^[:\s]+/, "") : r).trim().slice(0, 150);
    fillEvent(div, failed ? "tool_error" : "tool_result",
      tHtml(failed ? "log.tool_refused" : "log.tool_returned", { tool: e.tool, snippet }), e);
    div.title = t("log.click_tech");
    div.onclick = () => div.classList.toggle("show-tech");
    if (!div.parentNode) log.appendChild(div);
    return;
  }

  // Everything else: one plain-language line (details on click).
  const div = document.createElement("div");
  fillEvent(div, e.type, esc(noteFor(e) || e.type), e);
  div.title = t("log.click_tech");
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
      log.innerHTML = `<div class="empty">${esc(t("log.empty"))}</div>`;
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
    setHTML(box, `<div class="empty">${esc(t("calls.empty"))}</div>`);
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
  for (const task of (lastState?.tasks || [])) labelById[task.id] = tlabel(task);
  for (const c of calls) {
    const failed = c.id.includes("_error");
    const base = c.id.slice(0, 3);
    const taskId = callTask[base];
    const forTask = taskId != null
      ? " · " + t("calls.task", { label: labelById[taskId] ?? taskId }) : "";
    const forRound = callRound[base] != null
      ? " · " + t("calls.round", { n: callRound[base] }) : "";
    const div = document.createElement("div");
    div.className = "call-item" + (failed ? " failed" : "");
    const row = document.createElement("div");
    row.className = "call-row";
    const label = document.createElement("span");
    label.textContent = "▸ " + (failed
      ? t("calls.item_failed", { id: base })
      : t("calls.item", { id: c.id })) + forTask + forRound;
    const meta = document.createElement("span");
    meta.className = "sz";
    meta.textContent = [tokens[c.id], fmt1(c.size / 1024) + " kB"]
      .filter(Boolean).join(" · ");
    row.append(label, meta);
    div.append(row);
    if (tokensIn[c.id] != null) {
      const bar = document.createElement("div");
      bar.className = "call-bar";
      bar.title = t("calls.bar_tip", { n: tokensIn[c.id].toLocaleString(LANG) });
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
    setHTML($("memory-list"), `<div class="empty">${esc(t("memory.empty"))}</div>`);
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
    setHTML(box, `<div class="empty">${esc(t("workspace.empty"))}</div>`);
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
    size.textContent = fmt1(f.size / 1024) + " kB";
    div.append(label, size);
    div.addEventListener("click", () => openWorkspaceFile(f.name));
    box.appendChild(div);
  }
}

function renderTasks(tasks) {
  if (!tasks.length) {
    setHTML($("tasks-list"), `<div class="empty">${esc(t("tasks.empty"))}</div>`);
    return;
  }
  // Group by family: newest user task first, then its children (subtasks,
  // verifications — recursively) indented beneath it, oldest first.
  const children = (id) =>
    tasks.filter(c => c.parent === id).flatMap(c => [c, ...children(c.id)]);
  const roots = tasks.filter(x => x.parent == null).reverse();
  const ordered = roots.flatMap(r => [r, ...children(r.id)]);
  // Safety net: show orphans (parent missing) rather than losing them.
  for (const x of tasks) if (!ordered.includes(x)) ordered.push(x);

  const icons = { queued: "⏳", running: "▶", done: "✔", failed: "✘", stopped: "⏹" };
  setHTML($("tasks-list"), ordered.map(task => `
    <div class="history-item ${esc(task.status)} ${esc(task.kind)}"
         onclick="openTask(${Number(task.id)})" title="${esc(t("tasks.click"))}">
      <div class="hist-head">
        <span class="hist-icon">${icons[task.status] || "•"}</span>
        <span class="hist-task">${
          task.kind === "verification"
            ? esc(t("tasks.verification_of", { label: tlabel(task), parent: parentLabel(task) }))
          : task.kind === "subtask"
            ? `↳ ${esc(tlabel(task))} · ${esc(task.task || "")}`
            : `${esc(t("tasks.task", { label: tlabel(task) }))} · ${esc(task.task || "")}`}</span>
        ${task.status === "running" ? `<span class="hist-now">${esc(t("tasks.now"))}</span>` : ""}
        <span class="hist-time">${esc((task.created || "").slice(11, 16))}</span>
      </div>
      <div class="hist-result">${esc(outcomeText(task) ||
          (task.status === "queued" ? t("tasks.waiting") : ""))}
        ${task.iterations
          ? `<span class="hist-iters">· ${esc(t("tasks.rounds", { n: task.iterations }))}</span>`
          : ""}</div>
    </div>`).join(""));
}

// Click a task row: show its full description, outcome and STORY — the
// run retold round by round, subtasks and verifications included.
async function openTask(id) {
  const task = (lastState?.tasks || []).find(x => x.id === id);
  if (!task) return;
  $("call-modal-title").textContent = t("taskmodal.title", {
    label: tlabel(task),
    kind: t("kind." + task.kind),
    status: t("status." + task.status).toLowerCase(),
  });
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
  mk(t("taskmodal.description"), task.task);
  mk(t("taskmodal.outcome"), outcomeText(task) ||
     (task.status === "queued" ? t("taskmodal.still_queued") : t("taskmodal.still_running")));

  // The story is assembled server-side from the event log — in the
  // dashboard's language on request; the same text in English is in
  // agent_data/logs/narratives.md for every top-level task.
  const h = document.createElement("div");
  h.className = "call-section";
  h.textContent = t("taskmodal.story");
  const story = document.createElement("div");
  story.className = "story";
  story.innerHTML = `<div class="empty">${esc(t("taskmodal.loading"))}</div>`;
  box.append(h, story);
  $("call-modal").classList.remove("hidden");

  try {
    const data = await (await fetch(`/api/narrative/${Number(id)}?lang=${LANG}`)).json();
    story.innerHTML = renderStory(data.markdown || "");
  } catch (err) {
    story.innerHTML = `<div class="empty">${esc(t("taskmodal.load_error"))}</div>`;
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
  return html || `<div class="empty">${esc(t("taskmodal.nothing"))}</div>`;
}

// ---------------------------------------------------------------------------
// Interactions: run / stop / step / reset
// ---------------------------------------------------------------------------

// A server error: the JSON carries an English sentence plus a stable code
// (app.py api_error) — show the code's translation if we have one.
async function showError(res) {
  const d = await res.json();
  alert(I18N["error." + d.code] ? t("error." + d.code) : d.error);
}

async function submitTask() {
  const task = $("task-input").value.trim();
  if (!task) return;
  const res = await fetch("/api/task", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task }),
  });
  if (!res.ok) await showError(res);
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
  const el = e.target;
  if (el.matches("input, textarea, select") || el.isContentEditable) return;
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
  if (!res.ok) await showError(res);
  poll();
}
$("verify-check").onchange = (e) => saveSettings({ verify: e.target.checked });
$("plan-check").onchange = (e) => saveSettings({ plan: e.target.checked });
$("max-iter-input").onchange = (e) => saveSettings({ max_iterations: Number(e.target.value) });
// The model is a setting like any other — the pick lands in settings.json.
$("model-select").onchange = (e) => saveSettings({ model: e.target.value });

$("reset-btn").onclick = async () => {
  const res = await fetch("/api/reset", { method: "POST" });
  if (!res.ok) await showError(res);
  poll();
};

$("reset-all-btn").onclick = async () => {
  if (!confirm(t("confirm.reset_all"))) return;
  const res = await fetch("/api/reset_all", { method: "POST" });
  if (!res.ok) await showError(res);
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
  $("call-modal-title").textContent = t("callmodal.title", { id });
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
  $("call-modal-raw").textContent = t(showRaw ? "callmodal.show_pretty" : "callmodal.show_raw");
  const box = $("call-modal-content");

  // Fallback (or on request): the plaintext file exactly as on disk.
  if (showRaw || !currentCall.request) {
    box.innerHTML = `<pre class="raw">${esc(currentCall.raw)}</pre>`;
    return;
  }

  const req = currentCall.request;
  const usage = (currentCall.response && currentCall.response.usage) || null;
  const usageText = usage
    ? `&nbsp;·&nbsp; ${esc(t("callmodal.usage",
        { in: usage.prompt_tokens, out: usage.completion_tokens }))}` : "";
  const endpoint = currentCall.url
    ? ` &nbsp;·&nbsp; ${esc(t("callmodal.sent_to"))} <b>${esc(currentCall.url)}</b>` : "";
  let html = `<div class="call-meta">${esc(t("callmodal.model"))} <b>${esc(req.model)}</b>${endpoint}
              &nbsp;·&nbsp; ${esc(t("callmodal.messages", { n: req.messages.length }))}
              &nbsp;·&nbsp; ${esc(t("callmodal.response"))} ${esc(currentCall.status || "?")}${usageText}</div>`;

  html += `<div class="call-section">${esc(t("callmodal.request"))}</div>`;
  for (const m of req.messages) {
    html += m.role === "system" ? systemCard(m.content) : msgCard(m.role, m.content);
  }

  html += `<div class="call-section">${esc(t("callmodal.response_section"))}</div>`;
  const resp = currentCall.response;
  if (resp && resp.choices) {
    html += msgCard("assistant", resp.choices[0].message.content);
  } else {
    html += msgCard("error", typeof resp === "string" ? resp : JSON.stringify(resp, null, 2));
  }
  box.innerHTML = html;
}

// The role names (system / user / assistant) are the literal values from
// the API request — data, not chrome, so they stay as they are.
function msgCard(role, content) {
  return `<div class="msg-card role-${esc(role)}">
            <div class="msg-role">${esc(role)}</div>
            <pre class="msg-content">${esc(content)}</pre>
          </div>`;
}

// The system message is build_system_prompt()'s parts joined with "---".
// Split it back apart and label each part, so you can SEE that the prompt
// is just the brain files, the tool list and the memories concatenated.
// [display name (null = the tool list), description key in i18n.js]
const SYS_PARTS = [
  ["SYSTEM_PROMPT.md", "callmodal.part.system_prompt"],
  [null,               "callmodal.part.tools"],
  ["GHOST.md",         "callmodal.part.ghost"],
  ["KNOWLEDGE.md",     "callmodal.part.knowledge"],
  ["memory/*.md",      "callmodal.part.memory"],
];

function systemCard(content) {
  const parts = String(content).split("\n\n---\n\n");
  // Label only what we can identify with certainty — a brain file that
  // itself contains a "---" line would shift the labels, so fall back to
  // the plain one-block card rather than mislabel anything. The two
  // headings are prompt text (tools.py / agent.py) and stay English.
  const looksRight = (parts.length === 4 || parts.length === 5)
    && parts[1].startsWith("## Available tools")
    && (parts.length === 4 || parts[4].startsWith("## Your memories"));
  if (!looksRight) return msgCard("system", content);
  const segs = parts.map((p, i) => `
    <div class="sys-seg sys-seg-${i}">
      <div class="sys-seg-label">${esc(SYS_PARTS[i][0] || t("callmodal.tool_list"))} <span>— ${esc(t(SYS_PARTS[i][1]))}</span></div>
      <pre class="msg-content">${esc(p)}</pre>
    </div>`).join("");
  return `<div class="msg-card role-system">
            <div class="msg-role">${esc(t("callmodal.system_parts", { n: parts.length }))}</div>
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

// Language: EN | DE. Switching just navigates to ?lang=… — the page reloads
// and i18n.js remembers the choice in this browser. The dashboard keeps no
// state of its own worth saving; everything comes back from the files.
// (A live switch would have to clear lastHTML, reset the incremental event
// log and re-render any open dialog — exactly the hidden client state this
// dashboard claims not to have.)
document.querySelectorAll(".lang-btn").forEach(btn => {
  btn.classList.toggle("active", btn.dataset.lang === LANG);
  btn.onclick = () => {
    if (brainEditing && !confirm(t("confirm.lang_switch"))) return;   // unsaved brain edit
    location.search = "?lang=" + btn.dataset.lang;
  };
});

// ---------------------------------------------------------------------------
// Brain files: tabs + in-dashboard editing.
// Saving POSTs the text; the agent re-reads the files before every LLM
// call, so a save applies to the very next iteration — even mid-task.
// ---------------------------------------------------------------------------

document.querySelectorAll(".tab").forEach(tab => {
  tab.onclick = () => {
    if (brainEditing) return; // finish or cancel the edit first
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
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
  document.querySelectorAll(".tab").forEach(x => x.disabled = on);
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
  if (!res.ok) { await showError(res); return; }
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

// t() for the few templates that contain <b> tags (see i18n.js): the
// parameters come from the files, so they are escaped; the template is not.
function tHtml(key, params = {}) {
  const safe = {};
  for (const [k, v] of Object.entries(params)) safe[k] = esc(v);
  return t(key, safe);
}
