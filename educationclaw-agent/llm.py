"""
llm.py — talking to the language model.

The big lesson of this file: an "LLM call" is nothing magical.
It is ONE HTTP POST request with a JSON body, to any server that speaks
the OpenAI chat-completions format (OpenAI, Ollama, vLLM, OpenRouter...).

Every single call — the FULL request and the FULL response — is written
to agent_data/llm_calls/NNN.txt so you can read exactly what the model
saw and what it answered.

Several models can live in .env side by side (see load_models). Which
one is used is just another entry in settings.json — the dashboard's
model picker writes it, and every call reads it fresh, so you can even
switch models in the middle of a task.
"""

import json
import os
import re
import time
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from storage import (
    LLM_CALLS_DIR,
    STOP_FILE,
    flag_on,
    log_event,
    next_number,
    read_settings,
    read_status,
)

# Read the model configuration from the .env file next to this script.
load_dotenv()


class LLMError(Exception):
    """Raised when the LLM endpoint cannot be reached or returns garbage."""


# ---------------------------------------------------------------------------
# Model configuration — one or more provider/model combinations from .env
# ---------------------------------------------------------------------------


def load_models(env=None) -> list:
    """Read every configured model from the environment (.env).

    Model 1 is the classic single-model setup — plain BASE_URL, API_KEY
    and MODEL (plus the optional TEMPERATURE and MAX_TOKENS). An old .env
    with only those keys keeps working exactly as before.

    More models use the same keys with an LLM_<n>_ prefix:
    LLM_2_BASE_URL, LLM_2_API_KEY, LLM_2_MODEL, ... A model is discovered
    by its LLM_<n>_MODEL line; any other key it leaves out is taken from
    the plain keys, so a second model at the same provider needs only its
    MODEL line. LLM_<n>_LABEL is the optional name shown in the picker.

    Each entry: {"id", "label", "model", "base_url", "api_key",
                 "temperature", "max_tokens"}.
    """
    env = os.environ if env is None else env

    numbers = {1} if (env.get("MODEL") or "").strip() else set()
    for key in env:
        match = re.fullmatch(r"LLM_(\d+)_MODEL", key)
        if match and (env.get(key) or "").strip():
            numbers.add(int(match.group(1)))

    def get(n: int, key: str) -> str:
        """LLM_<n>_KEY — or the plain KEY as the shared default."""
        return (env.get(f"LLM_{n}_{key}") or env.get(key) or "").strip()

    models = []
    for n in sorted(numbers):
        models.append(
            {
                "id": str(n),
                "label": (env.get(f"LLM_{n}_LABEL") or "").strip(),  # never shared
                "model": get(n, "MODEL"),
                "base_url": get(n, "BASE_URL").rstrip("/"),
                "api_key": get(n, "API_KEY"),
                "temperature": _number(get(n, "TEMPERATURE"), float, f"TEMPERATURE (model {n})"),
                "max_tokens": _number(get(n, "MAX_TOKENS"), int, f"MAX_TOKENS (model {n})"),
            }
        )

    # Default labels: the model name — plus the host when the same name
    # is configured at two different endpoints, so the picker stays clear.
    names = [m["model"] for m in models]
    for m in models:
        if not m["label"]:
            m["label"] = m["model"]
            if names.count(m["model"]) > 1:
                m["label"] += f" @ {urlparse(m['base_url']).netloc or m['base_url'] or '?'}"
    return models


def _number(text: str, kind, name: str):
    """'0.7' -> 0.7, '' -> None; anything else is a config error worth a
    clear message at startup rather than a crash in the middle of a task."""
    if not text:
        return None
    try:
        return kind(text)
    except ValueError:
        raise ValueError(f"{name} in .env must be a number, not {text!r}.") from None


MODELS = load_models()


def model_by_id(model_id) -> dict | None:
    return next((m for m in MODELS if m["id"] == str(model_id)), None)


def current_model() -> dict | None:
    """The model picked in the dashboard (settings.json) — or the first
    configured one if nothing was picked or the pick no longer exists."""
    chosen = model_by_id(read_settings().get("model"))
    return chosen or (MODELS[0] if MODELS else None)


# ---------------------------------------------------------------------------
# The call itself
# ---------------------------------------------------------------------------


def call_llm(messages: list) -> str:
    """Send the conversation to the model, log everything, return its reply.

    `messages` is the standard chat format:
    [{"role": "system", "content": ...}, {"role": "user", "content": ...}, ...]
    """
    # Which model? Decided per call, so the dashboard's pick applies to
    # the very next request — even mid-task.
    model = current_model()
    if model is None:
        raise LLMError(
            "No model is configured — copy .env.example to .env, fill in "
            "BASE_URL, API_KEY and MODEL, and restart app.py."
        )
    if not model["base_url"]:
        raise LLMError(
            f"Model '{model['label']}' has no endpoint — set BASE_URL "
            f"(or LLM_{model['id']}_BASE_URL) in .env and restart app.py."
        )
    call_id = next_number(LLM_CALLS_DIR)
    url = f"{model['base_url']}/chat/completions"
    task_id = read_status().get("task_id")  # which task this call serves

    # This dict IS the API. Nothing more is sent.
    payload = {"model": model["model"], "messages": messages}
    if model["temperature"] is not None:
        payload["temperature"] = model["temperature"]
    if model["max_tokens"] is not None:
        payload["max_tokens"] = model["max_tokens"]

    log_event("llm_call_start", call_id=call_id, task_id=task_id, model=model["model"])

    # Try twice: local endpoints and workshop wifi hiccup sometimes.
    last_error = None
    for attempt in (1, 2):
        started = time.time()
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {model['api_key']}"},
                json=payload,
                timeout=60,
            )
            duration = time.time() - started
            data = response.json()
            _write_call_file(
                call_id, url, payload, f"{response.status_code} in {duration:.1f}s", data
            )

            if response.status_code != 200:
                raise LLMError(
                    f"Endpoint returned {response.status_code}: {json.dumps(data)[:300]}"
                )

            reply = data["choices"][0]["message"]["content"]
            # "usage" tells us how big the call was. Watching the prompt
            # tokens grow every iteration is half the lesson about context.
            usage = data.get("usage") or {}
            log_event(
                "llm_call_done",
                call_id=call_id,
                task_id=task_id,
                model=model["model"],
                duration=round(duration, 1),
                tokens_in=usage.get("prompt_tokens"),
                tokens_out=usage.get("completion_tokens"),
                preview=(reply or "")[:120],
            )
            return reply or ""

        except (
            requests.RequestException,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            LLMError,
        ) as exc:
            # (KeyError/IndexError/TypeError: the reply JSON is not in the
            # chat-completions shape; ValueError: the body was not JSON.)
            last_error = exc
            # Failed attempts get their own file (001_error1.txt) so the
            # record of what went wrong is never overwritten by a retry.
            _write_call_file(call_id, url, payload, "ERROR", str(exc), suffix=f"_error{attempt}")
            log_event("llm_call_error", call_id=call_id, attempt=attempt, error=str(exc)[:300])
            if attempt == 1:
                # Don't make the user wait through a retry they cancelled.
                if flag_on(STOP_FILE):
                    raise LLMError(
                        "Stop was pressed while the LLM call was failing — not retrying."
                    ) from exc
                time.sleep(2)  # brief pause, then one retry

    raise LLMError(f"LLM call failed twice. Last error: {last_error}")


def _write_call_file(
    call_id: int, url: str, payload: dict, status: str, response, suffix: str = ""
):
    """Write one human-readable file with the complete request + response."""
    if not isinstance(response, str):
        response = json.dumps(response, indent=2, ensure_ascii=False)
    text = (
        f"=== LLM CALL {call_id:03d} === {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
        f"\n--- REQUEST: POST {url} ---\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n"
        f"\n--- RESPONSE ({status}) ---\n"
        f"{response}\n"
    )
    (LLM_CALLS_DIR / f"{call_id:03d}{suffix}.txt").write_text(text, encoding="utf-8")
