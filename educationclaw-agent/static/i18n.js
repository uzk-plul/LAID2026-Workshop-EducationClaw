/*
 * i18n.js — every sentence the dashboard shows, in English and German.
 *
 * Only the dashboard's OWN words are translated. The prompts, the tool
 * descriptions, the tool results and everything the model writes stay
 * English: they are the conversation with the model, and the files in
 * agent_data/ are the record — the dashboard shows them as they are.
 *
 * Loaded in <head>, before the page is painted, so <html lang> is right
 * from the first frame. dashboard.js calls applyTranslations() once for
 * the static text and t() for everything it renders.
 */

// Which language? ?lang=de in the URL wins (and is remembered), then the
// remembered choice, then German. Switching = a reload with ?lang=… —
// the dashboard keeps no state of its own, everything comes back from the files.
const LANG = (() => {
  const fromUrl = new URLSearchParams(location.search).get("lang");
  let saved = null;
  try { saved = localStorage.getItem("lang"); } catch (e) {}
  const lang = [fromUrl, saved].find(l => l === "en" || l === "de") || "de";
  if (fromUrl === lang) { try { localStorage.setItem("lang", lang); } catch (e) {} }
  return lang;
})();
document.documentElement.lang = LANG;

// t("key", {name: value}) -> the sentence in the current language.
// {name} placeholders are filled from params; a plural entry looks like
// {"one": "…", "other": "…"} and is picked by params.n. Missing texts fall
// back to English, then to the key itself (visible on purpose).
function t(key, params = {}) {
  const entry = I18N[key];
  if (!entry) { console.warn("missing text:", key); return key; }
  let text = entry[LANG] ?? entry.en;
  if (typeof text === "object") text = params.n === 1 ? text.one : text.other;
  return text.replace(/\{(\w+)\}/g, (m, name) => (name in params ? String(params[name]) : m));
}

// Static page text: every element with data-i18n / data-i18n-title /
// data-i18n-placeholder is filled once at startup — and the "how it works"
// prose, written twice in index.html, shows the block of this language.
function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll(".help-content[lang]").forEach(el => { el.hidden = (el.lang !== LANG); });
}

// The table. Keep it JSON (double quotes, no trailing commas): test_agent.py
// reads it and checks that every key has both languages and that the
// English event sentences match storage.describe_event(). Full-line
// comments like this one are fine. German person-like roles use the
// gender star (Agent*in, Nutzer*innen, Planer*in).
const I18N = {
  // ---- header ----
  "title": { "en": "educationclaw-agent — Dashboard", "de": "educationclaw-agent — Dashboard" },
  "header.help": { "en": "? how it works", "de": "? so funktioniert's" },
  "header.help_tip": {
    "en": "A short explanation of how this agent works",
    "de": "Eine kurze Erklärung, wie diese*r Agent*in funktioniert"
  },
  "header.lang_tip": {
    "en": "Dashboard language — prompts, tool descriptions and the model's own text stay English",
    "de": "Sprache des Dashboards — Prompts, Tool-Beschreibungen und der Text des Modells bleiben Englisch"
  },
  "header.screen_tip": {
    "en": "The external screen — a separate program reads message.json, the set_screen tool writes it",
    "de": "Der externe Screen — ein separates Programm liest message.json, das Tool set_screen schreibt sie"
  },
  "header.model": { "en": "model:", "de": "Modell:" },
  "header.model_tip": {
    "en": "The model name sent in every request. With several models in .env this is a picker — the choice is saved to settings.json and applies to the next LLM call, even mid-task",
    "de": "Der Modellname, der in jeder Anfrage mitgeschickt wird. Mit mehreren Modellen in .env wird daraus eine Auswahl — die Wahl landet in settings.json und gilt für den nächsten LLM-Aufruf, auch mitten in einer Aufgabe"
  },
  "header.endpoint": { "en": "endpoint:", "de": "Endpunkt:" },
  "header.endpoint_tip": {
    "en": "The OpenAI-compatible endpoint of the selected model (from .env)",
    "de": "Der OpenAI-kompatible Endpunkt des gewählten Modells (aus .env)"
  },
  "header.key": { "en": "key:", "de": "Schlüssel:" },
  "header.tokens_label": { "en": "tokens:", "de": "Tokens:" },
  "header.tokens_tip": {
    "en": "Tokens the model read / wrote in this log (from the API's usage field)",
    "de": "Tokens, die das Modell in diesem Log gelesen / geschrieben hat (aus dem usage-Feld der API)"
  },
  "header.tokens": { "en": "{in} read / {out} written", "de": "{in} gelesen / {out} geschrieben" },
  "header.iteration": { "en": "iteration", "de": "Runde" },
  "header.not_set": { "en": "(not set)", "de": "(nicht gesetzt)" },

  // ---- status badge, screen mood, task kind (pixel font: keep them short) ----
  "status.idle": { "en": "IDLE", "de": "BEREIT" },
  "status.running": { "en": "RUNNING", "de": "LÄUFT" },
  "status.paused": { "en": "PAUSED", "de": "PAUSE" },
  "status.done": { "en": "DONE", "de": "FERTIG" },
  "status.failed": { "en": "FAILED", "de": "FEHLER" },
  "status.stopped": { "en": "STOPPED", "de": "GESTOPPT" },
  "status.verifying": { "en": "VERIFYING", "de": "PRÜFT" },
  "status.planning": { "en": "PLANNING", "de": "PLANT" },
  "mood.neutral": { "en": "NEUTRAL", "de": "NEUTRAL" },
  "mood.happy": { "en": "HAPPY", "de": "FROH" },
  "mood.sad": { "en": "SAD", "de": "TRAURIG" },
  "screen.empty": { "en": "(no message yet)", "de": "(noch keine Nachricht)" },
  "kind.user": { "en": "user task", "de": "Nutzer*innenaufgabe" },
  "kind.subtask": { "en": "subtask", "de": "Teilaufgabe" },
  "kind.verification": { "en": "verification", "de": "Prüfung" },

  // ---- task panel ----
  "panel.task": { "en": "Task", "de": "Aufgabe" },
  "task.placeholder": {
    "en": "Give the agent a task, e.g.: Check the current time, then set the screen to a happy greeting that mentions the time. (Ctrl+Enter runs it)",
    "de": "Eine Aufgabe für die*den Agent*in, z. B.: Prüfe die aktuelle Uhrzeit und setze dann den Screen auf einen fröhlichen Gruß, der die Uhrzeit nennt. (Strg+Enter startet sie)"
  },
  "task.run": { "en": "▶ Run task", "de": "▶ Aufgabe starten" },
  "task.queue": { "en": "＋ Queue task", "de": "＋ Einreihen" },
  "task.step": { "en": "⏭ Next step", "de": "⏭ Nächster Schritt" },
  "task.step_tip": {
    "en": "Run one more loop round (shortcut: Space or N)",
    "de": "Eine weitere Runde der Schleife ausführen (Tastenkürzel: Leertaste oder N)"
  },
  "task.stop": { "en": "⏹ Stop", "de": "⏹ Stopp" },
  "task.reset": { "en": "Reset logs", "de": "Logs zurücksetzen" },
  "task.reset_all": { "en": "Reset everything", "de": "Alles zurücksetzen" },
  "task.reset_all_tip": {
    "en": "Delete logs, LLM calls, memories, workspace files and restore the brain files — only .env is kept",
    "de": "Löscht Logs, LLM-Aufrufe, Erinnerungen und Arbeitsbereich-Dateien und stellt die Brain-Dateien wieder her — nur .env bleibt"
  },
  "task.step_mode": { "en": "Step-by-step", "de": "Schritt für Schritt" },
  "task.step_mode_tip": {
    "en": "Pause before every loop round until you press 'Next step' (or Space / N) — great for narrating what happens",
    "de": "Vor jeder Runde der Schleife anhalten, bis 'Nächster Schritt' (oder Leertaste / N) gedrückt wird — ideal, um das Geschehen zu kommentieren"
  },
  "task.plan": { "en": "Plan first", "de": "Erst planen" },
  "task.plan_tip": {
    "en": "Every user task first runs as a PLANNER that may only create subtasks (all other tools are refused) — the real work then happens in those subtasks",
    "de": "Jede Nutzer*innenaufgabe läuft zuerst als PLANER*IN, die*der nur Teilaufgaben anlegen darf (alle anderen Tools werden verweigert) — die eigentliche Arbeit passiert dann in diesen Teilaufgaben"
  },
  "task.verify": { "en": "Self-verification", "de": "Selbstprüfung" },
  "task.verify_tip": {
    "en": "Every finished user task queues a verification task — a fresh agent run that checks the work with its own tools and fixes what it finds",
    "de": "Jede abgeschlossene Nutzer*innenaufgabe reiht eine Prüfaufgabe ein — einen frischen Agent*innenlauf, der die Arbeit mit eigenen Tools kontrolliert und Gefundenes repariert"
  },
  "task.max_rounds": { "en": "max rounds", "de": "max. Runden" },
  "task.max_rounds_tip": {
    "en": "Hard cap on loop rounds — the agent re-reads this every round, so you can raise it while a task runs",
    "de": "Harte Obergrenze für die Runden der Schleife — die*der Agent*in liest sie jede Runde neu, sie lässt sich also auch während einer Aufgabe erhöhen"
  },
  "task.verifying": {
    "en": "Verifying the work of task {label}",
    "de": "Prüft die Arbeit von Aufgabe {label}"
  },
  "task.result_done": { "en": "done", "de": "erledigt" },
  "task.result_failed": { "en": "failed", "de": "fehlgeschlagen" },
  "task.stopped": { "en": "Stopped by you.", "de": "Per Stop-Knopf gestoppt." },
  "task.paused": {
    "en": "Paused — press 'Next step' for the next round.",
    "de": "Pausiert — 'Nächster Schritt' drücken für die nächste Runde."
  },
  "task.working": { "en": "Working — round {n}…", "de": "Arbeitet — Runde {n}…" },
  "task.current": { "en": "task {label}: {text}", "de": "Aufgabe {label}: {text}" },

  // ---- panel headings ----
  "panel.tasks": { "en": "Tasks", "de": "Aufgaben" },
  "panel.memory": { "en": "Memory", "de": "Gedächtnis" },
  "panel.workspace": { "en": "Workspace", "de": "Arbeitsbereich" },
  "panel.loop": { "en": "The agentic loop", "de": "Die Agent*innen-Schleife" },
  "panel.timeline": { "en": "Timeline", "de": "Zeitleiste" },
  "panel.log": { "en": "Event log", "de": "Ereignisprotokoll" },
  "panel.calls": { "en": "LLM calls", "de": "LLM-Aufrufe" },
  "panel.brain": { "en": "Brain files", "de": "Brain-Dateien" },
  "panel.tools": { "en": "Tools", "de": "Tools" },
  "tools.no_args": { "en": "no arguments", "de": "keine Argumente" },

  // ---- the loop diagram ----
  "loop.step.context": { "en": "build context", "de": "Kontext bauen" },
  "loop.step.ask": { "en": "ask the model", "de": "Modell fragen" },
  "loop.step.decide": { "en": "model picks a tool", "de": "Modell wählt ein Tool" },
  "loop.step.tool": { "en": "run the tool", "de": "Tool ausführen" },
  "loop.step.finish": { "en": "finish", "de": "fertig" },
  "loop.phase.iteration_start": {
    "en": "Pasting the brain files, tools, memories and the conversation into one big prompt…",
    "de": "Brain-Dateien, Tools, Erinnerungen und die Unterhaltung werden zu einem großen Prompt zusammengeklebt…"
  },
  "loop.phase.paused": {
    "en": "Paused before the next round — press 'Next step' to continue.",
    "de": "Pause vor der nächsten Runde — 'Nächster Schritt' drücken, um weiterzumachen."
  },
  "loop.phase.llm_call_start": {
    "en": "The whole prompt is on its way to the model — waiting for the answer…",
    "de": "Der ganze Prompt ist auf dem Weg zum Modell — warten auf die Antwort…"
  },
  "loop.phase.llm_call_error": {
    "en": "The model could not be reached — retrying…",
    "de": "Das Modell war nicht erreichbar — neuer Versuch…"
  },
  "loop.phase.llm_call_done": {
    "en": "Answer received — looking for the JSON tool call at the end of the reply…",
    "de": "Antwort da — Suche nach dem JSON-Tool-Aufruf am Ende der Antwort…"
  },
  "loop.phase.protocol_error": {
    "en": "The reply broke the one-tool-call rule — the model is told what happened and continues.",
    "de": "Die Antwort hat die Ein-Tool-Aufruf-Regel gebrochen — das Modell erfährt, was passiert ist, und macht weiter."
  },
  "loop.phase.tool_call": {
    "en": "The orchestrator is executing the tool the model chose…",
    "de": "Der Orchestrator führt das Tool aus, das das Modell gewählt hat…"
  },
  "loop.phase.tool_result": {
    "en": "Tool finished — its result is appended to the conversation, then the loop repeats.",
    "de": "Tool fertig — sein Ergebnis wird an die Unterhaltung angehängt, dann wiederholt sich die Schleife."
  },
  "loop.phase.task_done": {
    "en": "The model called 'finish' — the loop has ended.",
    "de": "Das Modell hat 'finish' aufgerufen — die Schleife ist zu Ende."
  },
  "loop.queued.verification": {
    "en": "A verification task was queued — a fresh run will now check this work.",
    "de": "Eine Prüfaufgabe wurde eingereiht — ein frischer Lauf kontrolliert jetzt diese Arbeit."
  },
  "loop.queued.subtask": {
    "en": "The model queued a subtask — it will run as its own fresh agent run later.",
    "de": "Das Modell hat eine Teilaufgabe eingereiht — sie läuft später als eigener frischer Agent*innenlauf."
  },
  "loop.queued.user": {
    "en": "A new task joined the queue — it starts as soon as the agent is free.",
    "de": "Eine neue Aufgabe steht in der Warteschlange — sie startet, sobald die*der Agent*in frei ist."
  },
  "loop.running_task": { "en": "running task {label}", "de": "läuft: Aufgabe {label}" },
  "loop.waiting_suffix": {
    "en": { "one": "{n} waiting in the queue", "other": "{n} waiting in the queue" },
    "de": { "one": "{n} wartet in der Warteschlange", "other": "{n} warten in der Warteschlange" }
  },
  "loop.waiting": {
    "en": { "one": "{n} task waiting in the queue", "other": "{n} tasks waiting in the queue" },
    "de": { "one": "{n} Aufgabe wartet in der Warteschlange", "other": "{n} Aufgaben warten in der Warteschlange" }
  },

  // ---- timeline chips ----
  "timeline.kind.task": { "en": "task", "de": "Aufgabe" },
  "timeline.kind.verify": { "en": "verify", "de": "Prüfung" },
  "timeline.kind.plan": { "en": "plan", "de": "Plan" },
  "timeline.tokens": { "en": "{in} tokens in, {out} out", "de": "{in} Tokens rein, {out} raus" },
  "timeline.click_open": { "en": "click to open", "de": "zum Öffnen klicken" },
  "timeline.protocol": { "en": "protocol", "de": "Protokoll" },
  "timeline.protocol_tip": {
    "en": "The reply broke the one-tool-call rule.",
    "de": "Die Antwort hat die Ein-Tool-Aufruf-Regel gebrochen."
  },
  "timeline.empty_plan": { "en": "empty plan", "de": "leerer Plan" },
  "timeline.empty_plan_tip": {
    "en": "Tried to finish planning without subtasks.",
    "de": "Wollte die Planung ohne Teilaufgaben beenden."
  },
  "timeline.done": { "en": "done", "de": "fertig" },
  "timeline.failed": { "en": "failed", "de": "gescheitert" },
  "timeline.stopped": { "en": "stopped", "de": "gestoppt" },
  "timeline.empty": { "en": "No steps yet.", "de": "Noch keine Schritte." },

  // ---- event log (chrome) ----
  "log.tech_toggle": {
    "en": "show technical details everywhere",
    "de": "technische Details überall zeigen"
  },
  "log.tech_toggle_tip": {
    "en": "Reveal the technical line (timestamps, event types, raw arguments) under every entry",
    "de": "Zeigt unter jedem Eintrag die technische Zeile (Zeitstempel, Ereignistyp, rohe Argumente)"
  },
  "log.empty": { "en": "No events yet.", "de": "Noch keine Ereignisse." },
  "log.task_divider": { "en": "⚑ TASK {label}: {text}", "de": "⚑ AUFGABE {label}: {text}" },
  "log.round": { "en": "· round {n} ·", "de": "· Runde {n} ·" },
  "log.asking": { "en": "Asking the model…", "de": "Frage das Modell…" },
  "log.open_call": { "en": "call #{id} ↗", "de": "Aufruf #{id} ↗" },
  "log.click_open": {
    "en": "Click to open the full request and response",
    "de": "Klicken, um Anfrage und Antwort vollständig zu sehen"
  },
  "log.click_tech": { "en": "Click for the technical details", "de": "Klicken für die technischen Details" },
  "log.tool_running": { "en": "Running <b>{tool}</b>…", "de": "Führe <b>{tool}</b> aus…" },
  "log.tool_refused": { "en": "<b>{tool}</b> refused — {snippet}", "de": "<b>{tool}</b> hat verweigert — {snippet}" },
  "log.tool_returned": { "en": "<b>{tool}</b> → {snippet}", "de": "<b>{tool}</b> → {snippet}" },

  // ---- event log: the sentences themselves. The English ones are exactly
  // ---- what storage.describe_event() writes into events.jsonl (a test
  // ---- checks that); the German ones re-tell them from the same fields.
  "event.task_start.verification": {
    "en": "A verification task starts: a fresh agent run checks the previous task's work with its own tools.",
    "de": "Eine Prüfaufgabe beginnt: ein frischer Agent*innenlauf kontrolliert die Arbeit der vorigen Aufgabe mit eigenen Tools."
  },
  "event.task_start.planning": {
    "en": "A new task came in — plan-first mode: the agent must break it into subtasks before any real work happens.",
    "de": "Eine neue Aufgabe ist da — Erst-planen-Modus: die*der Agent*in muss sie in Teilaufgaben zerlegen, bevor echte Arbeit passiert."
  },
  "event.task_start": {
    "en": "A new task came in — the agentic loop starts.",
    "de": "Eine neue Aufgabe ist da — die Agent*innen-Schleife startet."
  },
  "event.planning_incomplete": {
    "en": "The model tried to finish planning without creating a single subtask — sent back to make a real plan.",
    "de": "Das Modell wollte die Planung beenden, ohne eine einzige Teilaufgabe anzulegen — zurückgeschickt, um einen echten Plan zu machen."
  },
  "event.task_queued.verification": {
    "en": "Verification is on — the orchestrator queued a verification task to check this work.",
    "de": "Selbstprüfung ist eingeschaltet — der Orchestrator hat eine Prüfaufgabe eingereiht, um diese Arbeit zu kontrollieren."
  },
  "event.task_queued.subtask": {
    "en": "The model split off a subtask — it will run as its own fresh agent run when this task is finished.",
    "de": "Das Modell hat eine Teilaufgabe abgespalten — sie läuft als eigener frischer Agent*innenlauf, sobald diese Aufgabe fertig ist."
  },
  "event.task_queued": {
    "en": "Task added to the queue — it starts when the agent is free.",
    "de": "Aufgabe in die Warteschlange gestellt — sie startet, sobald die*der Agent*in frei ist."
  },
  "event.iteration_start": {
    "en": "Loop round {iteration}: rebuild the context, then ask the model what to do next.",
    "de": "Runde {iteration}: Kontext neu aufbauen, dann das Modell fragen, was als Nächstes zu tun ist."
  },
  "event.llm_call_start": {
    "en": "Sending the whole conversation to {model} (call #{call_id}).",
    "de": "Die ganze Unterhaltung geht an {model} (Aufruf #{call_id})."
  },
  "event.the_model": { "en": "the model", "de": "das Modell" },
  "event.llm_call_done.tokens": {
    "en": "The model answered after {duration}s — it read {tokens_in} tokens of context and wrote {tokens_out} tokens.",
    "de": "Das Modell hat nach {duration}s geantwortet — es hat {tokens_in} Tokens Kontext gelesen und {tokens_out} Tokens geschrieben."
  },
  "event.llm_call_done": {
    "en": "The model answered after {duration}s.",
    "de": "Das Modell hat nach {duration}s geantwortet."
  },
  "event.llm_call_error.first": {
    "en": "Could not reach the model — waiting briefly, then retrying.",
    "de": "Das Modell war nicht erreichbar — kurz warten, dann neuer Versuch."
  },
  "event.llm_call_error.again": {
    "en": "The model call failed a second time — giving up on this task.",
    "de": "Der Modellaufruf ist zum zweiten Mal fehlgeschlagen — diese Aufgabe wird aufgegeben."
  },
  "event.tool_call": {
    "en": "The model decided to use the tool '{tool}'.",
    "de": "Das Modell hat sich für das Tool '{tool}' entschieden."
  },
  "event.tool_result.refused": {
    "en": "'{tool}' refused with an error — the model will read it and can correct itself in the next round.",
    "de": "'{tool}' hat mit einem Fehler verweigert — das Modell liest ihn und kann sich in der nächsten Runde korrigieren."
  },
  "event.tool_result": {
    "en": "'{tool}' did its job and returned a result.",
    "de": "'{tool}' hat seine Arbeit getan und ein Ergebnis zurückgegeben."
  },
  "event.protocol_error.many": {
    "en": "The model sent {count} tool calls at once — the rule is one per round. Only the first was executed; it must send the others one at a time.",
    "de": "Das Modell hat {count} Tool-Aufrufe auf einmal geschickt — die Regel ist einer pro Runde. Nur der erste wurde ausgeführt; die anderen muss es einzeln senden."
  },
  "event.protocol_error": {
    "en": "The model's reply contained no valid tool call — sending it a reminder of the rules.",
    "de": "Die Antwort des Modells enthielt keinen gültigen Tool-Aufruf — es bekommt eine Erinnerung an die Regeln."
  },
  "event.task_done": {
    "en": "The model called 'finish' — the task is complete.",
    "de": "Das Modell hat 'finish' aufgerufen — die Aufgabe ist abgeschlossen."
  },
  "event.task_failed": { "en": "The task ended without success.", "de": "Die Aufgabe endete ohne Erfolg." },
  "event.task_stopped": {
    "en": "You pressed Stop — the task and everything queued were cancelled.",
    "de": "Stop wurde gedrückt — die Aufgabe und alles Wartende wurden abgebrochen."
  },
  "event.paused": {
    "en": "Step mode: pausing before the next round. Press 'Next step' to continue.",
    "de": "Schrittmodus: Pause vor der nächsten Runde. 'Nächster Schritt' drücken, um weiterzumachen."
  },

  // ---- LLM calls panel ----
  "calls.empty": { "en": "No LLM calls yet.", "de": "Noch keine LLM-Aufrufe." },
  "calls.item": { "en": "call #{id}", "de": "Aufruf #{id}" },
  "calls.item_failed": { "en": "call #{id} (failed attempt)", "de": "Aufruf #{id} (fehlgeschlagener Versuch)" },
  "calls.task": { "en": "task {label}", "de": "Aufgabe {label}" },
  "calls.round": { "en": "round {n}", "de": "Runde {n}" },
  "calls.bar_tip": {
    "en": "{n} tokens of context went into this call (the longest call in this list = full width)",
    "de": "{n} Tokens Kontext gingen in diesen Aufruf (der längste Aufruf in dieser Liste = volle Breite)"
  },

  // ---- memory, workspace, tasks panels ----
  "memory.empty": { "en": "Empty.", "de": "Leer." },
  "workspace.empty": { "en": "Empty.", "de": "Leer." },
  "tasks.empty": { "en": "No tasks yet.", "de": "Noch keine Aufgaben." },
  "tasks.click": { "en": "click for details", "de": "Klicken für Details" },
  "tasks.task": { "en": "Task {label}", "de": "Aufgabe {label}" },
  "tasks.verification_of": { "en": "↳ {label} · verification of {parent}", "de": "↳ {label} · Prüfung von {parent}" },
  "tasks.now": { "en": "NOW", "de": "JETZT" },
  "tasks.waiting": { "en": "waiting in the queue…", "de": "wartet in der Warteschlange…" },
  "tasks.rounds": {
    "en": { "one": "{n} round", "other": "{n} rounds" },
    "de": { "one": "{n} Runde", "other": "{n} Runden" }
  },

  // ---- the task dialog (description, outcome, the story) ----
  "taskmodal.title": { "en": "Task {label} · {kind} · {status}", "de": "Aufgabe {label} · {kind} · {status}" },
  "taskmodal.description": { "en": "TASK", "de": "AUFGABE" },
  "taskmodal.outcome": { "en": "OUTCOME", "de": "ERGEBNIS" },
  "taskmodal.still_queued": { "en": "Still waiting in the queue.", "de": "Wartet noch in der Warteschlange." },
  "taskmodal.still_running": { "en": "Still running.", "de": "Läuft noch." },
  "taskmodal.story": { "en": "THE STORY", "de": "DIE GESCHICHTE" },
  "taskmodal.loading": { "en": "Loading…", "de": "Lädt…" },
  "taskmodal.load_error": { "en": "Could not load the story.", "de": "Die Geschichte konnte nicht geladen werden." },
  "taskmodal.nothing": { "en": "Nothing to tell yet.", "de": "Noch nichts zu erzählen." },

  // ---- the orchestrator's outcome sentences, by error_code (tasks.json) ----
  "outcome.max_rounds": {
    "en": "Gave up after {n} rounds (the model never called finish).",
    "de": "Nach {n} Runden aufgegeben (das Modell hat nie finish aufgerufen)."
  },
  "outcome.stopped": { "en": "Stopped by user.", "de": "Per Stop-Knopf gestoppt." },
  "outcome.cancelled_stop": { "en": "Cancelled by Stop.", "de": "Durch Stop abgebrochen." },
  "outcome.restart_running": {
    "en": "The server restarted while this task was running.",
    "de": "Der Server wurde neu gestartet, während diese Aufgabe lief."
  },
  "outcome.restart_queued": { "en": "Cancelled by server restart.", "de": "Durch den Server-Neustart abgebrochen." },

  // ---- the LLM call dialog ----
  "callmodal.title": { "en": "LLM call #{id}", "de": "LLM-Aufruf #{id}" },
  "callmodal.show_raw": { "en": "show raw file", "de": "Rohdatei zeigen" },
  "callmodal.show_pretty": { "en": "show formatted", "de": "formatiert zeigen" },
  "callmodal.model": { "en": "model:", "de": "Modell:" },
  "callmodal.sent_to": { "en": "sent to:", "de": "gesendet an:" },
  "callmodal.messages": {
    "en": { "one": "{n} message sent", "other": "{n} messages sent" },
    "de": { "one": "{n} Nachricht gesendet", "other": "{n} Nachrichten gesendet" }
  },
  "callmodal.response": { "en": "response:", "de": "Antwort:" },
  "callmodal.usage": { "en": "{in} tokens read, {out} written", "de": "{in} Tokens gelesen, {out} geschrieben" },
  "callmodal.request": { "en": "REQUEST", "de": "ANFRAGE" },
  "callmodal.response_section": { "en": "RESPONSE", "de": "ANTWORT" },
  "callmodal.system_parts": {
    "en": "system — one prompt, assembled from {n} parts",
    "de": "system — ein Prompt, zusammengesetzt aus {n} Teilen"
  },
  "callmodal.tool_list": { "en": "tool list", "de": "Tool-Liste" },
  "callmodal.part.system_prompt": { "en": "the rules of the game", "de": "die Spielregeln" },
  "callmodal.part.tools": { "en": "generated from the registry in tools.py", "de": "erzeugt aus der Registry in tools.py" },
  "callmodal.part.ghost": { "en": "the persona", "de": "die Persönlichkeit" },
  "callmodal.part.knowledge": { "en": "injected facts", "de": "eingespeiste Fakten" },
  "callmodal.part.memory": { "en": "every saved memory", "de": "jede gespeicherte Erinnerung" },

  // ---- brain files, modals, confirmations ----
  "brain.edit": { "en": "✎ Edit", "de": "✎ Bearbeiten" },
  "brain.save": { "en": "Save", "de": "Speichern" },
  "brain.cancel": { "en": "Cancel", "de": "Abbrechen" },
  "modal.close": { "en": "✕ close", "de": "✕ schließen" },
  "help.title": { "en": "How this agent works", "de": "So funktioniert diese*r Agent*in" },
  "confirm.reset_all": {
    "en": "Reset EVERYTHING? Logs, LLM calls, memories, workspace files and the brain files all go back to a fresh install. Only .env is kept.",
    "de": "Wirklich ALLES zurücksetzen? Logs, LLM-Aufrufe, Erinnerungen, Arbeitsbereich-Dateien und die Brain-Dateien gehen zurück auf den Auslieferungszustand. Nur .env bleibt."
  },
  "confirm.lang_switch": {
    "en": "Switch the language now? The page reloads and the unsaved brain-file edit is lost.",
    "de": "Jetzt die Sprache wechseln? Die Seite lädt neu und die ungespeicherte Änderung an der Brain-Datei geht verloren."
  },

  // ---- server errors, by the code app.py sends next to its English sentence ----
  "error.empty_task": { "en": "Task text is empty.", "de": "Die Aufgabe ist leer." },
  "error.unknown_model": {
    "en": "unknown model — see the LLM_<n>_ keys in .env",
    "de": "Unbekanntes Modell — siehe die LLM_<n>_-Einträge in .env"
  },
  "error.invalid_settings": { "en": "invalid settings", "de": "Ungültige Einstellungen" },
  "error.unknown_file": { "en": "unknown file", "de": "Unbekannte Datei" },
  "error.busy": {
    "en": "Cannot reset while a task is running.",
    "de": "Zurücksetzen geht nicht, während eine Aufgabe läuft."
  },
  "error.no_such_call": { "en": "no such call", "de": "Kein solcher Aufruf" },
  "error.no_such_task": { "en": "no such task", "de": "Keine solche Aufgabe" },
  "error.no_such_file": { "en": "no such file", "de": "Keine solche Datei" }
};
