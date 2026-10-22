"""Talk to a local Ollama model, and refuse to guess when the prompt won't fit.

Ollama's default behaviour on an oversized prompt is to drop tokens from the
front and answer anyway. For this harness that is the worst possible failure:
the schema block and the instructions fall off, the question survives, and the
model answers from general knowledge of what an NBA database probably looks
like. The result is a plausible query, a clean-looking run record, and a
silently wrong verdict.

So every call sends `truncate: false`, which makes the server return 400
instead of truncating, and every call records `prompt_eval_count` — the tokens
the model actually read. Prompt caching changes that call's latency but not
the count, so it stays a true record of what the model saw.

Only the Ollama wire protocol lives here. Prompt construction is the caller's
business.
"""

import json
import time
import urllib.error
import urllib.request

# Re-exported: the judge and older callers name them as `ollama.LLMError` etc.
from agent.completion import Completion, ContextOverflowError, LLMError

GENERATE_PATH = "/api/generate"
SHOW_PATH = "/api/show"
TAGS_PATH = "/api/tags"

# A cold call reads the whole prompt before generating; a big schema block plus
# a full output can run to a minute or more on local hardware.
DEFAULT_TIMEOUT_S = 300.0


def _post(url: str, body: dict, timeout_s: float) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        if "exceed_context_size" in detail or "exceeds the available context" in detail:
            raise ContextOverflowError(detail) from error
        raise LLMError(f"{url} returned {error.code}: {detail[:400]}") from error
    except urllib.error.URLError as error:
        raise LLMError(f"cannot reach {url}: {error.reason}. Is `ollama serve` running?") from error


def generate(config, prompt: str, system: str | None = None,
             timeout_s: float = DEFAULT_TIMEOUT_S) -> Completion:
    """One completion, with the run's settings applied and overflow refused."""
    body = {
        "model": config.model,
        "prompt": prompt,
        "stream": False,
        # Refuse rather than truncate. Without this the call would succeed and
        # lie about what the model read.
        "truncate": False,
        "options": {
            "num_ctx": config.num_ctx,
            "num_predict": config.max_output_tokens,
            "temperature": config.temperature,
            "seed": config.seed,
        },
    }
    if system is not None:
        body["system"] = system

    started = time.monotonic()
    payload = _post(config.ollama_host.rstrip("/") + GENERATE_PATH, body, timeout_s)
    elapsed = time.monotonic() - started

    return Completion(
        text=payload.get("response", ""),
        model=payload.get("model", config.model),
        prompt_tokens=payload.get("prompt_eval_count", 0),
        output_tokens=payload.get("eval_count", 0),
        elapsed_s=elapsed,
        done_reason=payload.get("done_reason", ""),
    )


def model_context_length(config, timeout_s: float = 30.0) -> int | None:
    """The model's own trained context length, or None if it isn't reported."""
    payload = _post(config.ollama_host.rstrip("/") + SHOW_PATH, {"model": config.model}, timeout_s)
    info = payload.get("model_info") or {}
    for key, value in info.items():
        if key.endswith("context_length"):
            return int(value)
    return None


def preflight(config, prompt: str, system: str | None = None) -> Completion:
    """Check the run can happen at all, before it burns ten minutes finding out.

    Catches the two misconfigurations that would otherwise surface as wrong
    answers: a `num_ctx` the model cannot honour, and a prompt that does not
    fit the window.
    """
    limit = model_context_length(config)
    if limit is not None and config.num_ctx > limit:
        raise LLMError(
            f"num_ctx {config.num_ctx:,} exceeds what {config.model} supports "
            f"({limit:,}). Ollama would clamp it without saying so."
        )

    # One token of output: this is about whether the prompt is readable, not
    # about what the model says.
    probe = generate(config, prompt, system=system)
    if probe.prompt_tokens >= config.num_ctx:
        raise ContextOverflowError(
            f"prompt read as {probe.prompt_tokens:,} tokens against num_ctx "
            f"{config.num_ctx:,} — no room to generate"
        )
    return probe


def available_models(config, timeout_s: float = 30.0) -> list[str]:
    """Models the server has pulled, for a legible error when one is missing."""
    url = config.ollama_host.rstrip("/") + TAGS_PATH
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            payload = json.load(response)
    except urllib.error.URLError as error:
        raise LLMError(f"cannot reach {url}: {error.reason}. Is `ollama serve` running?") from error
    return sorted(m["name"] for m in payload.get("models", []))
