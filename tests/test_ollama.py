"""The client must refuse a prompt that doesn't fit, not answer anyway.

Most tests here fake the wire so they run without a server. The few that need
a real model are marked `integration` and skip when Ollama isn't reachable.
"""

import json
import urllib.error
import urllib.request

import pytest

from agent import ollama
from evals import config


@pytest.fixture
def cfg():
    return config.from_dict({
        "name": "t", "model": "qwen2.5-coder:14b",
        "prompt_version": "v1", "schema_mode": "columns",
        "num_ctx": 4096, "max_output_tokens": 32, "seed": 7, "temperature": 0.0,
    })


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_wire(monkeypatch, payload=None, error=None, capture=None):
    def _urlopen(request, timeout=None):
        if capture is not None:
            capture["url"] = request.full_url
            capture["body"] = json.loads(request.data.decode())
            capture["timeout"] = timeout
        if error is not None:
            raise error
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)


def http_error(code, detail):
    return urllib.error.HTTPError(
        "http://x", code, "err", {}, __import__("io").BytesIO(detail.encode())
    )


# --- the overflow guard ----------------------------------------------------

def test_every_call_refuses_truncation(monkeypatch, cfg):
    captured = {}
    fake_wire(monkeypatch, payload={"response": "ok", "done_reason": "stop"}, capture=captured)
    ollama.generate(cfg, "hello")
    # Without this the server drops the front of the prompt and answers anyway.
    assert captured["body"]["truncate"] is False


def test_context_overflow_raises_its_own_error(monkeypatch, cfg):
    detail = '{"error":{"code":400,"message":"request (4033 tokens) exceeds the available context size (2048 tokens)","type":"exceed_context_size_error"}}'
    fake_wire(monkeypatch, error=http_error(400, detail))
    with pytest.raises(ollama.ContextOverflowError):
        ollama.generate(cfg, "x" * 100)


def test_other_http_errors_are_not_mistaken_for_overflow(monkeypatch, cfg):
    fake_wire(monkeypatch, error=http_error(404, '{"error":"model not found"}'))
    with pytest.raises(ollama.LLMError) as caught:
        ollama.generate(cfg, "hello")
    assert not isinstance(caught.value, ollama.ContextOverflowError)


def test_unreachable_server_says_so(monkeypatch, cfg):
    fake_wire(monkeypatch, error=urllib.error.URLError("Connection refused"))
    with pytest.raises(ollama.LLMError, match="ollama serve"):
        ollama.generate(cfg, "hello")


# --- what the run record gets ----------------------------------------------

def test_completion_carries_the_counts_the_run_record_needs(monkeypatch, cfg):
    fake_wire(monkeypatch, payload={
        "response": "SELECT 1", "model": "qwen2.5-coder:14b",
        "prompt_eval_count": 1539, "eval_count": 12, "done_reason": "stop",
    })
    done = ollama.generate(cfg, "hello")
    assert done.text == "SELECT 1"
    assert done.prompt_tokens == 1539
    assert done.output_tokens == 12
    assert done.elapsed_s >= 0
    assert not done.output_truncated


def test_a_cut_off_answer_is_visible(monkeypatch, cfg):
    # done_reason "length" means max_output_tokens stopped it mid-answer.
    fake_wire(monkeypatch, payload={"response": "SELECT ", "done_reason": "length"})
    assert ollama.generate(cfg, "hello").output_truncated


def test_run_settings_are_actually_sent(monkeypatch, cfg):
    captured = {}
    fake_wire(monkeypatch, payload={"response": "ok"}, capture=captured)
    ollama.generate(cfg, "hello", system="be terse")
    options = captured["body"]["options"]
    assert options["num_ctx"] == cfg.num_ctx
    assert options["num_predict"] == cfg.max_output_tokens
    assert options["seed"] == cfg.seed
    assert options["temperature"] == cfg.temperature
    assert captured["body"]["system"] == "be terse"
    assert captured["body"]["stream"] is False


# --- preflight -------------------------------------------------------------

def test_preflight_rejects_a_num_ctx_the_model_cannot_honour(monkeypatch, cfg):
    # Ollama clamps silently, so the config would be a request, not a fact.
    fake_wire(monkeypatch, payload={"model_info": {"qwen2.context_length": 2048}})
    with pytest.raises(ollama.LLMError, match="exceeds what"):
        ollama.preflight(cfg, "hello")


def test_preflight_rejects_a_prompt_that_fills_the_window(monkeypatch, cfg):
    calls = []

    def _urlopen(request, timeout=None):
        calls.append(request.full_url)
        if request.full_url.endswith(ollama.SHOW_PATH):
            return FakeResponse({"model_info": {"qwen2.context_length": 32768}})
        return FakeResponse({"response": "ok", "prompt_eval_count": cfg.num_ctx})

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    with pytest.raises(ollama.ContextOverflowError, match="no room to generate"):
        ollama.preflight(cfg, "hello")


def test_preflight_passes_on_a_healthy_setup(monkeypatch, cfg):
    def _urlopen(request, timeout=None):
        if request.full_url.endswith(ollama.SHOW_PATH):
            return FakeResponse({"model_info": {"qwen2.context_length": 32768}})
        return FakeResponse({"response": "ok", "prompt_eval_count": 40, "done_reason": "stop"})

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    assert ollama.preflight(cfg, "hello").prompt_tokens == 40


def test_missing_context_length_does_not_block_the_run(monkeypatch, cfg):
    # Not every model reports one; absence is not evidence of a problem.
    fake_wire(monkeypatch, payload={"model_info": {}})
    assert ollama.model_context_length(cfg) is None


# --- against a real server -------------------------------------------------

def ollama_up() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/version", timeout=2):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


integration = pytest.mark.skipif(not ollama_up(), reason="ollama serve not running")


@integration
def test_real_server_refuses_an_oversized_prompt():
    # The behaviour the whole module is built on. If a future Ollama silently
    # truncates again, this is the test that catches it.
    small = config.from_dict({
        "name": "t", "model": "qwen2.5-coder:14b", "prompt_version": "v1",
        "schema_mode": "columns", "num_ctx": 2048, "max_output_tokens": 8,
    })
    with pytest.raises(ollama.ContextOverflowError):
        ollama.generate(small, "word " * 4000)


@integration
def test_real_server_reports_the_full_prompt_even_when_cached():
    cfg = config.load("local-columns")
    prompt = "Context: " + ("alpha beta gamma delta. " * 200) + "\nReply with the single word OK."
    first = ollama.generate(cfg, prompt)
    second = ollama.generate(cfg, prompt)
    # Caching changes latency, not the count. If that ever stops being true,
    # prompt_tokens stops being a record of what the model read.
    assert first.prompt_tokens == second.prompt_tokens > 0
