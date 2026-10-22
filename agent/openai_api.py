"""Talk to an OpenAI model through the Chat Completions API.

The comparison arm for Phase 7. Same contract as `ollama.py`: one call in, one
`Completion` out, and a prompt that doesn't fit is an error, never a guess.
OpenAI refuses an oversized prompt on its own, so there is no `truncate` flag
to send — only its error code to recognise.

Two things differ from Ollama and both reach the run record:

- Reasoning models fix `temperature` at 1 and reject anything else. The config
  has to say 1, so the header records what the model actually ran at.
- `max_completion_tokens` caps hidden reasoning and the visible answer
  together. A cap sized for qwen can be spent entirely on reasoning, leaving
  an empty reply with `done_reason` "length".

The key comes from the environment (`OPENAI_API_KEY`), never from config.
"""

import json
import os
import time
import urllib.error
import urllib.request

from agent.completion import Completion, ContextOverflowError, LLMError

DEFAULT_BASE_URL = "https://api.openai.com/v1"
CHAT_PATH = "/chat/completions"
MODELS_PATH = "/models"

DEFAULT_TIMEOUT_S = 300.0

# A rate limit or a server hiccup is the network's fault, not the model's
# answer, and must not land in the record as an error verdict. Retried a few
# times with backoff; anything else fails at once.
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_WAITS_S = (2.0, 8.0, 30.0)


def _base_url() -> str:
    return os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _headers() -> dict:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise LLMError("OPENAI_API_KEY is not set (it belongs in .env)")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _request(path: str, body: dict | None, timeout_s: float) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    for wait in (*RETRY_WAITS_S, None):
        request = urllib.request.Request(_base_url() + path, data=data, headers=_headers())
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            # Out of credit is also a 429, and waiting won't fix it.
            if error.code in RETRY_STATUSES and "insufficient_quota" not in detail and wait:
                time.sleep(wait)
                continue
            if "context_length_exceeded" in detail:
                raise ContextOverflowError(detail) from error
            raise LLMError(f"{path} returned {error.code}: {detail[:400]}") from error
        except urllib.error.URLError as error:
            raise LLMError(f"cannot reach {_base_url()}: {error.reason}") from error
    raise AssertionError("unreachable")


def generate(config, prompt: str, system: str | None = None,
             timeout_s: float = DEFAULT_TIMEOUT_S) -> Completion:
    """One completion, with the run's settings applied."""
    messages = [{"role": "user", "content": prompt}]
    if system is not None:
        messages.insert(0, {"role": "system", "content": system})
    body = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "seed": config.seed,
        "max_completion_tokens": config.max_output_tokens,
    }

    started = time.monotonic()
    payload = _request(CHAT_PATH, body, timeout_s)
    elapsed = time.monotonic() - started

    choice = (payload.get("choices") or [{}])[0]
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    return Completion(
        text=(choice.get("message") or {}).get("content") or "",
        model=payload.get("model", config.model),
        prompt_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        elapsed_s=elapsed,
        done_reason=choice.get("finish_reason") or "",
        reasoning_tokens=details.get("reasoning_tokens", 0),
        cached_tokens=prompt_details.get("cached_tokens", 0),
    )


def preflight(config, prompt: str, system: str | None = None) -> Completion:
    """Check the run can happen at all: the key works, the model is there,
    and the schema prompt is accepted."""
    try:
        _request(f"{MODELS_PATH}/{config.model}", None, timeout_s=30.0)
    except LLMError as error:
        raise LLMError(f"model {config.model} is not available to this key: {error}") from error
    return generate(config, prompt, system=system)
