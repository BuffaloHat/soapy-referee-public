"""Traces have the agreed shape, carry the join keys, and stay off by default.

Spans go to an in-memory exporter, so no backend needs to be running. The model
is faked; the snapshot, gold SQL and comparator are real. Lesson numbers refer
to docs/observability_tools.md §6, where each wiring choice is explained.
"""

import json

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from agent import llm
from evals import cases as case_loader
from evals import config, db, record, resolve, runner, tracing


@pytest.fixture(scope="module")
def con():
    return db.connect()


@pytest.fixture(scope="module")
def resolver(con):
    return resolve.Resolver.from_snapshot(con)


@pytest.fixture
def exporter():
    memory = InMemorySpanExporter()
    tracing.configure({"memory": memory}, processor=SimpleSpanProcessor)
    yield memory
    tracing.shutdown()


@pytest.fixture
def case():
    return next(c for c in case_loader.load_cases() if c.expects == "rows" and c.check_sql)


def fake_model(monkeypatch, text):
    completion = llm.Completion(text=text, model="fake", prompt_tokens=2412,
                                   output_tokens=42, elapsed_s=0.01, done_reason="stop")
    monkeypatch.setattr(runner.llm, "generate", lambda *a, **k: completion)


def traced_case(monkeypatch, exporter, con, resolver, case, sql):
    fake_model(monkeypatch, sql)
    cfg = config.load("local-docs")
    row = runner.run_case(con, cfg, case, resolver, "schema", attempt=2, run_id="run-123")
    spans = exporter.get_finished_spans()
    return row, {s.name: s for s in spans}, spans


def metadata(span) -> dict:
    return json.loads(span.attributes["metadata"])


# --- shape -----------------------------------------------------------------

def test_the_trace_is_named_by_case_and_attempt(monkeypatch, exporter, con, resolver, case):
    # Three attempts of one case are told apart in every tool's list (lesson 14).
    _, by_name, _ = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    assert f"{case.id} #2" in by_name


def test_one_case_is_one_trace_with_the_steps_beneath_it(
        monkeypatch, exporter, con, resolver, case):
    _, by_name, spans = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    root = by_name[f"{case.id} #2"]
    assert root.parent is None
    assert {s.context.trace_id for s in spans} == {root.context.trace_id}

    steps = [s.name for s in spans if s.parent and s.parent.span_id == root.context.span_id]
    # Gold and agent queries are named apart, not told apart by an attribute (lesson 6).
    assert steps == ["build_prompt", "generate_sql", "execute_sql.gold",
                     "execute_sql.agent", "compare_rows"]


# --- join keys and metadata ------------------------------------------------

def test_the_root_joins_back_to_the_run_record(monkeypatch, exporter, con, resolver, case):
    row, by_name, _ = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    root = by_name[f"{case.id} #2"]
    assert root.attributes["session.id"] == "run-123"
    meta = metadata(root)
    assert (meta["run_id"], meta["case_id"], meta["attempt"]) == ("run-123", case.id, 2)
    assert meta["verdict"] == row.verdict
    assert meta["session_id"] == "run-123"          # smith's thread key (lesson 3)
    assert root.attributes["input.value"] == case.question


def test_each_query_step_shows_its_answer(monkeypatch, exporter, con, resolver, case):
    # The rows themselves, not just a count, so gold and agent read side by side.
    _, by_name, _ = traced_case(monkeypatch, exporter, con, resolver, case,
                                "SELECT NULL AS avg_points")
    assert by_name["execute_sql.agent"].attributes["output.value"] == "1 row\navg_points\nNULL"
    assert by_name["execute_sql.gold"].attributes["output.value"].startswith(
        f"{case.cardinality:,} row")


def test_the_run_record_row_carries_its_trace_ids(monkeypatch, exporter, con, resolver, case):
    # The other direction of the join: scores and labels find the trace by these.
    row, by_name, _ = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    root = by_name[f"{case.id} #2"]
    assert row.trace_id == f"{root.context.trace_id:032x}"
    assert row.span_id == f"{root.context.span_id:016x}"


def test_every_step_carries_the_join_keys(monkeypatch, exporter, con, resolver, case):
    # Any step can be filtered across runs and traced back to its row (lesson 7).
    _, _, spans = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    for span in spans:
        meta = metadata(span)
        assert (meta["run_id"], meta["case_id"], meta["attempt"]) == ("run-123", case.id, 2), span.name


def test_custom_fields_live_in_metadata_not_loose_attributes(
        monkeypatch, exporter, con, resolver, case):
    # smith drops attributes it does not recognise; metadata survives (lesson 5).
    _, by_name, spans = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    assert not [k for s in spans for k in s.attributes if k.startswith("sref.")]
    assert metadata(by_name["execute_sql.agent"])["role"] == "agent"
    assert metadata(by_name["execute_sql.gold"])["truncated"] is False
    assert metadata(by_name["generate_sql"])["done_reason"] == "stop"


def test_every_step_carries_the_session(monkeypatch, exporter, con, resolver, case):
    # fuse's session cost sums only steps with the session id; cost is on the
    # LLM step, so a root-only session showed $0.00.
    _, _, spans = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    assert {s.attributes.get("session.id") for s in spans} == {"run-123"}


def test_metadata_is_also_written_as_fuse_fields(monkeypatch, exporter, con, resolver, case):
    # fuse reads metadata as fields only under its own prefix.
    row, by_name, _ = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    root = by_name[f"{case.id} #2"].attributes
    assert root[f"{tracing.FUSE_METADATA}.verdict"] == row.verdict
    assert root[f"{tracing.FUSE_METADATA}.run_id"] == "run-123"


def test_the_root_carries_each_tools_labels(monkeypatch, exporter, con, resolver, case):
    _, by_name, _ = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    root = by_name[f"{case.id} #2"].attributes
    model = "qwen2.5-coder:14b"
    assert list(root["tag.tags"]) == ["local-docs", model, case.tier, "attempt-2"]
    assert root["langsmith.span.tags"] == f"local-docs,{model},{case.tier},attempt-2"  # lesson 4
    assert root[f"{tracing.FUSE_METADATA}.model"] == model
    assert root[f"{tracing.FUSE_METADATA}.provider"] == "ollama"
    assert root["langfuse.version"] == "v1"                            # lesson 11
    assert root["langfuse.release"] == record.git_commit()


def test_the_resource_names_the_environment_and_release(exporter, monkeypatch, con, resolver, case):
    _, by_name, _ = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    resource = by_name[f"{case.id} #2"].resource.attributes
    assert resource["deployment.environment.name"] == "dev"            # lesson 12
    assert resource["service.version"] == record.git_commit()


# --- the LLM span ----------------------------------------------------------

def test_the_llm_span_carries_model_tokens_and_both_messages(
        monkeypatch, exporter, con, resolver, case):
    _, by_name, _ = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    llm = by_name["generate_sql"].attributes
    assert llm["openinference.span.kind"] == "LLM"
    assert llm["llm.model_name"] == "qwen2.5-coder:14b"
    assert llm["llm.token_count.prompt"] == 2412
    assert llm["llm.token_count.total"] == 2454
    assert llm["llm.input_messages.0.message.role"] == "system"
    assert llm["output.value"] == case.check_sql
    # input.value holds both messages, so tools that display it show the
    # system prompt too (lesson 2).
    assert llm["input.mime_type"] == "application/json"
    roles = [m["role"] for m in json.loads(llm["input.value"])["messages"]]
    assert roles == ["system", "user"]
    # Ollama reports neither, so neither is sent as a misleading 0.
    assert "llm.token_count.prompt_details.cache_read" not in llm
    assert "llm.token_count.completion_details.reasoning" not in llm


def test_cached_and_reasoning_tokens_reach_the_llm_span(exporter):
    # OpenAI reports both; cost in fuse is wrong without the cached share.
    completion = llm.Completion(text="SELECT 1", model="gpt-6.1-sol", prompt_tokens=14735,
                                output_tokens=340, elapsed_s=0.01, done_reason="stop",
                                reasoning_tokens=300, cached_tokens=14592)
    with tracing.span("generate_sql", tracing.LLM) as current:
        tracing.record_completion(current, completion)
    attributes = exporter.get_finished_spans()[0].attributes
    assert attributes["llm.token_count.prompt_details.cache_read"] == 14592
    assert attributes["llm.token_count.completion_details.reasoning"] == 300


# --- status and errors -----------------------------------------------------

def test_a_clean_case_is_marked_ok_not_unset(monkeypatch, exporter, con, resolver, case):
    _, _, spans = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    assert all(s.status.status_code is StatusCode.OK for s in spans)    # lesson 10


def test_failing_agent_sql_is_an_error_on_the_step_and_the_case(
        monkeypatch, exporter, con, resolver, case):
    row, by_name, _ = traced_case(monkeypatch, exporter, con, resolver, case,
                                  "SELECT * FROM no_such_table")
    agent = by_name["execute_sql.agent"]
    assert agent.status.status_code is StatusCode.ERROR
    assert any(e.name == "exception" for e in agent.events)

    root = by_name[f"{case.id} #2"]
    assert row.verdict == "error"
    assert root.status.status_code is StatusCode.ERROR
    # Caught below, so the root records its own exception event for tools
    # that flag errors only from events (lesson 1).
    events = [e for e in root.events if e.name == "exception"]
    assert events and "agent SQL failed" in events[0].attributes["exception.message"]


# --- switched off and switched on ------------------------------------------

def test_nothing_is_traced_until_configured(monkeypatch, con, resolver, case):
    tracing.shutdown()
    fake_model(monkeypatch, case.check_sql)
    with tracing.span("probe", tracing.CHAIN) as span:
        assert not span.is_recording()
    row = runner.run_case(con, config.load("local-docs"), case, resolver, "schema", attempt=1)
    assert (row.trace_id, row.span_id) == (None, None)


def test_a_backend_is_on_only_when_its_settings_are_present(monkeypatch):
    for name in ("PHOENIX_COLLECTOR_ENDPOINT", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
                 "LANGFUSE_HOST", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert tracing.exporters_from_env() == {}

    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3100")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-x")
    # Half-configured Langfuse stays off rather than failing every export.
    assert sorted(tracing.exporters_from_env()) == ["phoenix"]

    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-x")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-x")
    assert sorted(tracing.exporters_from_env()) == ["langfuse", "langsmith", "phoenix"]


# --- environment -----------------------------------------------------------

def test_environment_defaults_to_dev_and_reads_sref_env(monkeypatch):
    monkeypatch.delenv("SREF_ENV", raising=False)
    assert record.environment() == "dev"
    monkeypatch.setenv("SREF_ENV", "eval")
    assert record.environment() == "eval"


@pytest.mark.parametrize("bad", ["Eval", "prod env", "langfuse-x", "x" * 41])
def test_an_unusable_environment_is_rejected(monkeypatch, bad):
    monkeypatch.setenv("SREF_ENV", bad)
    with pytest.raises(ValueError, match="SREF_ENV"):
        record.environment()


# --- fuse experiments ------------------------------------------------------

EXPERIMENT_KEYS = ("langfuse.experiment.id", "langfuse.experiment.name",
                   "langfuse.experiment.dataset.id", "langfuse.experiment.item.id",
                   "langfuse.experiment.item.root_observation_id")


def experiment_case(monkeypatch, exporter, con, resolver, case, sql):
    with tracing.experiment(config.load("local-docs"), "run-123", 2, "ds-1"):
        return traced_case(monkeypatch, exporter, con, resolver, case, sql)


def test_no_experiment_attributes_outside_an_experiment(monkeypatch, exporter, con, resolver, case):
    _, _, spans = traced_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    assert not any(k.startswith("langfuse.experiment") for s in spans for k in s.attributes)


def test_every_span_of_an_item_trace_names_the_experiment_and_item(
        monkeypatch, exporter, con, resolver, case):
    _, by_name, spans = experiment_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    root = by_name[f"{case.id} #2"]
    for s in spans:
        assert all(k in s.attributes for k in EXPERIMENT_KEYS), s.name
        assert s.attributes["langfuse.experiment.name"] == "run-123 #2"
        assert s.attributes["langfuse.experiment.dataset.id"] == "ds-1"
        assert s.attributes["langfuse.experiment.item.id"] == case.id
        # fuse finds the item's root by this; it must be the root's own span id.
        assert (s.attributes["langfuse.experiment.item.root_observation_id"]
                == f"{root.context.span_id:016x}")


def test_the_item_root_carries_question_gold_and_agent_answers(
        monkeypatch, exporter, con, resolver, case):
    row, by_name, _ = experiment_case(monkeypatch, exporter, con, resolver, case, case.check_sql)
    root = by_name[f"{case.id} #2"].attributes
    assert root["langfuse.observation.input"] == case.question
    # Expected is the gold result executed in this run, not a stored answer.
    assert json.loads(root["langfuse.experiment.item.expected_output"]) == \
        record.preview_text(row.gold_preview)
    assert root["langfuse.observation.output"] == record.preview_text(row.agent_preview)
    assert root["output.value"] == case.check_sql        # the SQL stays for the other tools
    assert "langfuse.experiment.item.expected_output" not in by_name["generate_sql"].attributes


def test_a_refusal_case_expects_a_refusal():
    row = record.CaseRow(case_id="c", tier="refusal", expects="refusal", attempt=1,
                         verdict="refusal_correct", refusal="no salary data")
    expected, actual = tracing.answers(row)
    assert expected.startswith("(declines")
    assert actual == "(declines) no salary data"
