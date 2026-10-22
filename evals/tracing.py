"""Traces of each run, sent to Phoenix, Langfuse and LangSmith at once.

One instrumentation layer, one exporter per backend. The spans use
OpenInference attribute names, which all three read, so the same trace shows
up in each without per-tool code. Which backends receive it is decided by the
environment — a backend is on when its settings are in `.env` — because where
traces go is not a run variable: it changes nothing about the answer.

Shape: one trace per case attempt, grouped into a session per run.

    <case_id> #<attempt>  CHAIN      question in, SQL out, verdict in metadata
    ├─ build_prompt       CHAIN
    ├─ generate_sql       LLM        messages, model, tokens, settings
    ├─ execute_sql.gold   TOOL
    ├─ execute_sql.agent  TOOL
    └─ compare_rows       EVALUATOR

A run is a session rather than one big trace because each tool's session view
is built for exactly that, and a run-sized trace would sit open for minutes.

The run record stays the source of truth. Every span carries `run_id`,
`case_id` and `attempt` in its metadata, so any step joins back to its row.

OpenInference is the shared vocabulary, but each tool keeps a different
subset of it (docs/observability_tools.md §6). Where a tool needs its own
attribute to show something, it is set here alongside the shared one — the
lesson numbers in comments point at §6. Everything worth seeing goes in
`metadata`, the one field every tool keeps.

Until `configure` is called, every span is a no-op. Tests and plain library
use never send anything anywhere.
"""

import base64
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar

from openinference.semconv.trace import (
    MessageAttributes,
    OpenInferenceSpanKindValues,
    SpanAttributes,
)
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Status, StatusCode

from evals import record

DEFAULT_PROJECT = "soapy-referee"

KIND = SpanAttributes.OPENINFERENCE_SPAN_KIND
CHAIN = OpenInferenceSpanKindValues.CHAIN.value
LLM = OpenInferenceSpanKindValues.LLM.value
TOOL = OpenInferenceSpanKindValues.TOOL.value
EVALUATOR = OpenInferenceSpanKindValues.EVALUATOR.value

# fuse reads metadata as fields only under its own prefix; phx and smith
# parse the OpenInference `metadata` JSON instead. Both are written (lesson 5).
FUSE_METADATA = "langfuse.observation.metadata"

_provider: TracerProvider | None = None
_tracer: trace.Tracer = trace.NoOpTracer()
_release: str | None = None

# The join keys of the case being traced, inherited by every step (lesson 7).
_case_keys: ContextVar[dict | None] = ContextVar("sref_case_keys", default=None)

# fuse v4 builds an experiment from attributes on the traces themselves, not
# from a link made afterwards. Experiment and item identity go on every span of
# an item trace — what fuse's BaggageSpanProcessor pattern does, done directly.
# None outside an experiment.
EXPERIMENT = "langfuse.experiment"
_experiment: ContextVar[dict | None] = ContextVar("sref_experiment", default=None)


# --- setup -----------------------------------------------------------------

def exporters_from_env() -> dict[str, SpanExporter]:
    """One OTLP exporter per backend whose settings are present."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    project = os.environ.get("LANGSMITH_PROJECT", DEFAULT_PROJECT)
    exporters = {}

    if phoenix := os.environ.get("PHOENIX_COLLECTOR_ENDPOINT"):
        exporters["phoenix"] = OTLPSpanExporter(endpoint=f"{phoenix.rstrip('/')}/v1/traces")

    public, secret = os.environ.get("LANGFUSE_PUBLIC_KEY"), os.environ.get("LANGFUSE_SECRET_KEY")
    if public and secret and (host := os.environ.get("LANGFUSE_HOST")):
        auth = base64.b64encode(f"{public}:{secret}".encode()).decode()
        exporters["langfuse"] = OTLPSpanExporter(
            endpoint=f"{host.rstrip('/')}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {auth}"},
        )

    if key := os.environ.get("LANGSMITH_API_KEY"):
        base = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
        exporters["langsmith"] = OTLPSpanExporter(
            endpoint=f"{base.rstrip('/')}/otel/v1/traces",
            headers={"x-api-key": key, "Langsmith-Project": project},
        )
    return exporters


def configure(exporters: dict[str, SpanExporter], processor=BatchSpanProcessor) -> list[str]:
    """Start sending spans to these exporters. Returns the backend names."""
    global _provider, _tracer, _release
    shutdown()
    project = os.environ.get("LANGSMITH_PROJECT", DEFAULT_PROJECT)
    _release = record.git_commit()
    resource = {
        "service.name": "sref",
        # Phoenix files traces by this; the others ignore it.
        "openinference.project.name": project,
        # Standard OTel resource attributes. fuse reads the environment into
        # its Environment filter (lesson 12).
        "deployment.environment.name": record.environment(),
    }
    if _release:
        resource["service.version"] = _release
    _provider = TracerProvider(resource=Resource.create(resource))
    for exporter in exporters.values():
        _provider.add_span_processor(processor(exporter))
    _tracer = _provider.get_tracer("sref")
    return sorted(exporters)


def shutdown() -> None:
    """Flush whatever is buffered and go back to no-op."""
    global _provider, _tracer
    if _provider is not None:
        _provider.shutdown()
    _provider, _tracer = None, trace.NoOpTracer()


# --- spans -----------------------------------------------------------------

def add_metadata(current, **values) -> None:
    """Merge keys into a span's metadata, in both forms the tools read."""
    if not current.is_recording():
        return
    values = {k: v for k, v in values.items() if v is not None}
    existing = json.loads(current.attributes.get(SpanAttributes.METADATA, "{}"))
    current.set_attribute(SpanAttributes.METADATA, json.dumps(existing | values))
    for key, value in values.items():
        current.set_attribute(f"{FUSE_METADATA}.{key}", value)


@contextmanager
def span(name: str, kind: str, input_value: str | None = None,
         metadata: dict | None = None, **attributes):
    """A span with an OpenInference kind and the current case's join keys.

    An exception passing through is recorded and marks the span ERROR. A span
    that finishes cleanly with no status set is marked OK, so the tools show
    "succeeded" rather than "unset" (lesson 10).
    """
    with _tracer.start_as_current_span(name) as current:
        current.set_attribute(KIND, kind)
        if input_value is not None:
            current.set_attribute(SpanAttributes.INPUT_VALUE, input_value)
        for key, value in ((_experiment.get() or {}) | attributes).items():
            if value is not None:
                current.set_attribute(key, value)
        keys = _case_keys.get() or {}
        # On every step, not just the root: fuse totals a session's cost over
        # the steps that carry its id, and cost sits on the LLM steps.
        if keys.get("session_id"):
            current.set_attribute(SpanAttributes.SESSION_ID, keys["session_id"])
        add_metadata(current, **(keys | (metadata or {})))
        yield current
        if current.is_recording() and current.status.status_code is StatusCode.UNSET:
            current.set_status(Status(StatusCode.OK))


def set_output(current, value) -> None:
    if value is not None:
        current.set_attribute(SpanAttributes.OUTPUT_VALUE, str(value))


def experiment_name(run_id: str, attempt: int) -> str:
    return f"{run_id} #{attempt}"


@contextmanager
def experiment(cfg, run_id: str, attempt: int, dataset_id: str):
    """One fuse experiment: every case trace of one attempt.

    One per attempt because fuse expects one trace per dataset item. Attempt 1
    of every arm is the unshuffled prompt, so `#1` against `#1` is the direct
    comparison; `#2` and `#3` show the spread.
    """
    settings = {"run_id": run_id, "attempt": attempt, "config": cfg.name,
                "provider": cfg.provider, "model": cfg.model,
                "prompt_version": cfg.prompt_version, "schema_mode": cfg.schema_mode,
                "temperature": cfg.temperature}
    attributes = {
        f"{EXPERIMENT}.id": f"{run_id}-attempt-{attempt}",
        f"{EXPERIMENT}.name": experiment_name(run_id, attempt),
        f"{EXPERIMENT}.dataset.id": dataset_id,
        **{f"{EXPERIMENT}.metadata.{k}": str(v) for k, v in settings.items()},
    }
    token = _experiment.set(attributes)
    try:
        yield
    finally:
        _experiment.reset(token)


def _start_item(current, case, cfg):
    """Mark a case root as its experiment item; returns the reset token for
    the item context its steps inherit, or None outside an experiment."""
    shared = _experiment.get()
    if shared is None or not current.is_recording():
        return None
    item = {
        f"{EXPERIMENT}.item.id": case.id,
        f"{EXPERIMENT}.item.root_observation_id": f"{current.get_span_context().span_id:016x}",
    }
    root_only = {
        "langfuse.observation.input": case.question,
        f"{EXPERIMENT}.description": f"{cfg.name}: {cfg.provider} {cfg.model}",
        f"{EXPERIMENT}.item.metadata.tier": case.tier,
        f"{EXPERIMENT}.item.metadata.expects": case.expects,
    }
    for key, value in (item | root_only).items():
        current.set_attribute(key, value)
    return _experiment.set(shared | item)


@contextmanager
def case_span(case, cfg, run_id: str, attempt: int):
    """The root of one case attempt's trace."""
    keys = {
        "run_id": run_id, "case_id": case.id, "attempt": attempt, "config": cfg.name,
        # smith groups traces into threads by this metadata key; it does not
        # read OpenInference `session.id` (lesson 3).
        "session_id": run_id,
    }
    # The model as a tag too: the case name is the same in every arm, and the
    # model is otherwise only on the LLM steps, which trace lists don't filter.
    tags = [cfg.name, cfg.model, case.tier, f"attempt-{attempt}"]
    token = _case_keys.set(keys)
    try:
        # The attempt is in the name as well as the metadata: a repeated run
        # lists each case three times, and only the name shows in every
        # tool's list without opening the trace (lesson 14).
        with span(f"{case.id} #{attempt}", CHAIN, input_value=case.question,
                  metadata={"tier": case.tier, "prompt_version": cfg.prompt_version,
                            "provider": cfg.provider, "model": cfg.model}, **{
                      SpanAttributes.SESSION_ID: run_id,
                      SpanAttributes.TAG_TAGS: tags,
                      # smith's tags: comma-separated, its own key (lesson 4).
                      "langsmith.span.tags": ",".join(tags),
                      # fuse's Release and Version filters (lesson 11).
                      "langfuse.release": _release,
                      "langfuse.version": cfg.prompt_version,
                  }) as current:
            item_token = _start_item(current, case, cfg)
            try:
                yield current
            finally:
                if item_token is not None:
                    _experiment.reset(item_token)
    finally:
        _case_keys.reset(token)


def ids(current) -> tuple[str | None, str | None]:
    """A span's trace and span id as OTel hex, or Nones when not tracing."""
    context = current.get_span_context()
    if not context.is_valid:
        return None, None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


def answers(row) -> tuple[str, str]:
    """Expected and actual answer as text, for an experiment's side-by-side.

    Expected is the gold result just executed, never a stored copy.
    """
    if row.expects == "refusal":
        expected = "(declines: the database cannot answer this)"
    else:
        expected = record.preview_text(row.gold_preview) if row.gold_preview else "(gold failed)"
    if row.refusal is not None:
        actual = f"(declines) {row.refusal}"
    elif row.agent_preview is not None:
        actual = record.preview_text(row.agent_preview)
    else:
        actual = f"(no rows) {row.error or row.detail}"
    return expected, actual


def finish_case(current, row) -> None:
    """Output and verdict, set once the row is known."""
    set_output(current, row.sql or row.refusal or row.error)
    add_metadata(current, verdict=row.verdict, detail=row.detail)
    if _experiment.get() is not None and current.is_recording():
        # fuse shows these as the item's expected and actual output; the SQL
        # stays in `output.value` and on the generate_sql step.
        expected, actual = answers(row)
        current.set_attribute(f"{EXPERIMENT}.item.expected_output", json.dumps(expected))
        current.set_attribute("langfuse.observation.output", actual)
    if row.verdict == "error":
        message = f"{row.detail}: {row.error}" if row.error else row.detail
        # The exception was caught below this span, so nothing was recorded
        # here. smith flags errors only from exception events, not status,
        # so without one the case shows green on its first screen (lesson 1).
        current.add_event("exception", {
            "exception.type": "sref.CaseError", "exception.message": message or "error",
        })
        current.set_status(Status(StatusCode.ERROR, row.detail or "error"))


def invocation_parameters(cfg) -> dict:
    """The settings as each provider's API names them."""
    if cfg.provider == "openai":
        return {"temperature": cfg.temperature, "seed": cfg.seed,
                "max_completion_tokens": cfg.max_output_tokens}
    return {"temperature": cfg.temperature, "seed": cfg.seed,
            "num_ctx": cfg.num_ctx, "num_predict": cfg.max_output_tokens}


@contextmanager
def llm_span(cfg, built, name: str = "generate_sql"):
    """The model call: messages in, settings, and (via `record_completion`) out."""
    message = MessageAttributes
    messages = [{"role": "system", "content": built.system},
                {"role": "user", "content": built.user}]
    attributes = {
        SpanAttributes.LLM_MODEL_NAME: cfg.model,
        SpanAttributes.LLM_PROVIDER: cfg.provider,
        SpanAttributes.LLM_INVOCATION_PARAMETERS: json.dumps(invocation_parameters(cfg)),
        SpanAttributes.LLM_PROMPT_TEMPLATE_VERSION: built.version,
        # smith and fuse display `input.value`, not the message list, so a
        # plain user string hid the system prompt (lesson 2). Both messages go
        # in as JSON; phx reads the message attributes below.
        SpanAttributes.INPUT_MIME_TYPE: "application/json",
    }
    for i, m in enumerate(messages):
        attributes[f"{SpanAttributes.LLM_INPUT_MESSAGES}.{i}.{message.MESSAGE_ROLE}"] = m["role"]
        attributes[f"{SpanAttributes.LLM_INPUT_MESSAGES}.{i}.{message.MESSAGE_CONTENT}"] = m["content"]
    with span(name, LLM, input_value=json.dumps({"messages": messages}),
              **attributes) as current:
        yield current


def record_completion(current, completion) -> None:
    message = MessageAttributes
    set_output(current, completion.text)
    current.set_attribute(f"{SpanAttributes.LLM_OUTPUT_MESSAGES}.0.{message.MESSAGE_ROLE}", "assistant")
    current.set_attribute(f"{SpanAttributes.LLM_OUTPUT_MESSAGES}.0.{message.MESSAGE_CONTENT}", completion.text)
    current.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_PROMPT, completion.prompt_tokens)
    current.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_COMPLETION, completion.output_tokens)
    current.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_TOTAL,
                          completion.prompt_tokens + completion.output_tokens)
    add_metadata(current, done_reason=completion.done_reason)
    if completion.reasoning_tokens:
        current.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_COMPLETION_DETAILS_REASONING,
                              completion.reasoning_tokens)
    if completion.cached_tokens:
        current.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_READ,
                              completion.cached_tokens)
