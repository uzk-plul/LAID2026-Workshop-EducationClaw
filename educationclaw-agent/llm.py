"""
llm.py — talking to the language model.

The big lesson of this file: an "LLM call" is nothing magical.
It is ONE HTTP POST request with a JSON body, to any server that speaks
the OpenAI chat-completions format (OpenAI, Ollama, vLLM, OpenRouter...).

Every single call — the FULL request and the FULL response — is written
to agent_data/llm_calls/NNN.txt so you can read exactly what the model
saw and what it answered.
"""

import json
import os
import time

import requests
from dotenv import load_dotenv

from storage import LLM_CALLS_DIR, STOP_FILE, flag_on, log_event, next_number, read_status

# Read BASE_URL, API_KEY and MODEL from the .env file next to this script.
load_dotenv()
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # e.g. http://localhost:1234/v1
API_KEY = os.getenv("API_KEY", "")
MODEL = os.getenv("MODEL", "")
TEMPERATURE = os.getenv("TEMPERATURE", "")  # optional, e.g. 0.7
MAX_TOKENS = os.getenv("MAX_TOKENS", "")  # optional, e.g. 1000


class LLMError(Exception):
    """Raised when the LLM endpoint cannot be reached or returns garbage."""


def call_llm(messages: list) -> str:
    """Send the conversation to the model, log everything, return its reply.

    `messages` is the standard chat format:
    [{"role": "system", "content": ...}, {"role": "user", "content": ...}, ...]
    """
    if not BASE_URL or not MODEL:
        raise LLMError(
            "BASE_URL and MODEL are not set — copy .env.example "
            "to .env, fill it in and restart app.py."
        )
    call_id = next_number(LLM_CALLS_DIR)
    url = f"{BASE_URL}/chat/completions"
    task_id = read_status().get("task_id")  # which task this call serves

    # This dict IS the API. Nothing more is sent.
    payload = {"model": MODEL, "messages": messages}
    if TEMPERATURE:
        payload["temperature"] = float(TEMPERATURE)
    if MAX_TOKENS:
        payload["max_tokens"] = int(MAX_TOKENS)

    log_event("llm_call_start", call_id=call_id, task_id=task_id)

    # Try twice: local endpoints and workshop wifi hiccup sometimes.
    last_error = None
    for attempt in (1, 2):
        started = time.time()
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {API_KEY}"},
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
