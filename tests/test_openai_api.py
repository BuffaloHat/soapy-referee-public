"""The OpenAI client keeps the Ollama client's contract: settings sent as
configured, overflow refused, and the counts the run record needs.

The wire is faked throughout, so these run without a key and cost nothing.
"""

import io
import json
import urllib.error
import urllib.request

import pytest

from agent import llm, openai_api
from evals import config, tracing


@pytest.fixture
def cfg():
    return config.from_dict({
        "name": "t", "provider": "openai", "model": "gpt-6.1-sol",
        "prompt_version": "v1", "schema_mode": "columns",
        "max_output_tokens": 4096, "seed": 7, "temperature": 1.0,
    })


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(openai_api.time, "sleep", lambda s: None)


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, detail):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(detail.encode()))


def fake_wire(monkeypatch, *replies, calls=None):
    """Each call gets the next reply: a payload dict, or an exception to raise."""
    queue = iter(replies)

    def _urlopen(request, timeout=None):
        if calls is not None:
            body = json.loads(request.data.decode()) if request.data else None
            calls.append({"url": request.full_url, "body": body,
                          "auth": request.get_header("Authorization")})
        reply = next(queue)
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(reply)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)


def chat(text="SELECT 1", finish="stop", prompt=1539, output=12, reasoning=0, cached=0):
    return {
        "model": "gpt-6.1-sol",
        "choices": [{"message": {"role": "assistant", "content": text},
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": output,
                  "prompt_tokens_details": {"cached_tokens": cached},
                  "completion_tokens_details": {"reasoning_tokens": reasoning}},
    }


# --- what is sent ----------------------------------------------------------

def test_run_settings_are_actually_sent(monkeypatch, cfg):
    calls = []
    fake_wire(monkeypatch, chat(), calls=calls)
    openai_api.generate(cfg, "hello", system="be terse")
    body = calls[0]["body"]
    assert calls[0]["url"].endswith(openai_api.CHAT_PATH)
    assert calls[0]["auth"] == "Bearer sk-test"
    assert body["model"] == "gpt-6.1-sol"
    assert body["temperature"] == cfg.temperature
    assert body["seed"] == cfg.seed
    # Reasoning models reject `max_tokens`; this cap covers reasoning + answer.
    assert body["max_completion_tokens"] == cfg.max_output_tokens
    assert body["messages"] == [{"role": "system", "content": "be terse"},
                                {"role": "user", "content": "hello"}]


def test_a_missing_key_fails_before_any_call(monkeypatch, cfg):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(llm.LLMError, match="OPENAI_API_KEY"):
        openai_api.generate(cfg, "hello")


# --- what comes back -------------------------------------------------------

def test_completion_carries_the_counts_the_run_record_needs(monkeypatch, cfg):
    fake_wire(monkeypatch, chat(prompt=17020, output=340, reasoning=300, cached=16896))
    done = openai_api.generate(cfg, "hello")
    assert done.text == "SELECT 1"
    assert done.prompt_tokens == 17020
    assert done.output_tokens == 340
    assert done.reasoning_tokens == 300
    # A subset of prompt_tokens, priced lower; cost can't be read without it.
    assert done.cached_tokens == 16896
    assert not done.output_truncated


def test_a_cap_spent_on_reasoning_is_visible(monkeypatch, cfg):
    # Seen live: max_completion_tokens 5 went entirely to reasoning, no text.
    fake_wire(monkeypatch, chat(text="", finish="length", output=5, reasoning=5))
    done = openai_api.generate(cfg, "hello")
    assert done.text == ""
    assert done.output_truncated
    assert done.reasoning_tokens == 5


# --- failures --------------------------------------------------------------

def test_context_overflow_raises_its_own_error(monkeypatch, cfg):
    detail = '{"error":{"code":"context_length_exceeded","message":"too long"}}'
    fake_wire(monkeypatch, http_error(400, detail))
    with pytest.raises(llm.ContextOverflowError):
        openai_api.generate(cfg, "hello")


def test_a_rejected_setting_is_an_error_not_an_overflow(monkeypatch, cfg):
    detail = "{\"error\":{\"message\":\"'temperature' does not support 0 with this model\"}}"
    fake_wire(monkeypatch, http_error(400, detail))
    with pytest.raises(llm.LLMError) as caught:
        openai_api.generate(cfg, "hello")
    assert not isinstance(caught.value, llm.ContextOverflowError)
    assert "temperature" in str(caught.value)


def test_a_rate_limit_is_waited_out(monkeypatch, cfg):
    calls = []
    fake_wire(monkeypatch, http_error(429, '{"error":{"code":"rate_limit_exceeded"}}'),
              http_error(503, "busy"), chat(), calls=calls)
    assert openai_api.generate(cfg, "hello").text == "SELECT 1"
    assert len(calls) == 3


def test_running_out_of_credit_is_not_retried(monkeypatch, cfg):
    calls = []
    fake_wire(monkeypatch, http_error(429, '{"error":{"code":"insufficient_quota"}}'),
              calls=calls)
    with pytest.raises(llm.LLMError, match="insufficient_quota"):
        openai_api.generate(cfg, "hello")
    assert len(calls) == 1


def test_retries_give_up_eventually(monkeypatch, cfg):
    calls = []
    busy = [http_error(503, "busy") for _ in range(len(openai_api.RETRY_WAITS_S) + 1)]
    fake_wire(monkeypatch, *busy, calls=calls)
    with pytest.raises(llm.LLMError, match="503"):
        openai_api.generate(cfg, "hello")
    assert len(calls) == len(openai_api.RETRY_WAITS_S) + 1


def test_preflight_names_a_model_the_key_cannot_use(monkeypatch, cfg):
    fake_wire(monkeypatch, http_error(404, '{"error":{"code":"model_not_found"}}'))
    with pytest.raises(llm.LLMError, match="not available to this key"):
        openai_api.preflight(cfg, "hello")


def test_preflight_checks_the_model_then_reads_the_prompt(monkeypatch, cfg):
    calls = []
    fake_wire(monkeypatch, {"id": "gpt-6.1-sol"}, chat(prompt=40), calls=calls)
    assert openai_api.preflight(cfg, "hello").prompt_tokens == 40
    assert calls[0]["url"].endswith("/models/gpt-6.1-sol")


# --- routing ---------------------------------------------------------------

def test_the_config_picks_the_provider(monkeypatch, cfg):
    fake_wire(monkeypatch, chat(text="from openai"))
    assert llm.generate(cfg, "hello").text == "from openai"


def test_an_unknown_provider_is_refused(cfg):
    # Config validation stops this first; the router checks too.
    bogus = type("Cfg", (), {"provider": "nope"})()
    with pytest.raises(llm.LLMError, match="unknown provider"):
        llm.generate(bogus, "hello")


def test_every_config_provider_has_a_client():
    assert set(llm.PROVIDERS) == config.PROVIDERS


def test_traces_name_the_settings_the_provider_was_sent(cfg):
    params = tracing.invocation_parameters(cfg)
    assert params == {"temperature": 1.0, "seed": 7, "max_completion_tokens": 4096}
