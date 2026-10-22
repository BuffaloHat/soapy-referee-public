"""Run the gold set against a model and write down what happened.

One config, one pass over the cases. Gold SQL and agent SQL go through the
same connection against the same snapshot, so a difference in verdict is a
difference in the answer and nothing else.

The steps are named and thin — `build_prompt`, `generate_sql`, `execute_sql`,
`compare_rows` — because Phase 4 wraps each one in a span. Keeping them
separate now means tracing decorates this file rather than rewriting it.

Cases run in one pass per config on purpose: the schema block is a constant
prefix, Ollama caches it, and the first call on the `docs` arm costs ~110s
against ~1s for the rest. Interleaving configs would pay that on every case.

Attempts are the outer loop for the same reason. With a perturbation each
attempt gets its own schema block, so each pays one cold call, not every case.
"""

import argparse
import dataclasses
import sys
import traceback
from contextlib import nullcontext
from pathlib import Path

from dotenv import load_dotenv

from agent import llm, prompt, schema_context
from evals import cases as case_loader
from evals import comparator, config, dataset, db, record, resolve, snapshot, tracing

# Refusals have no rows to compare, so they get their own outcomes rather than
# being forced through the comparator's vocabulary.
REFUSAL_CORRECT = "refusal_correct"      # asked to decline, declined
REFUSAL_ABSENT = "refusal_absent"        # should have declined, answered anyway
REFUSAL_UNEXPECTED = "refusal_unexpected"  # should have answered, declined

PASSING = frozenset({comparator.EXACT, comparator.TOLERANCE, REFUSAL_CORRECT})


def shuffle_seed(cfg, attempt: int) -> int | None:
    """The table-order seed for one attempt, or None for catalog order.

    Attempt 1 is always catalog order, so it is the same prompt as any
    single-attempt run of this config and directly comparable with one.
    """
    if cfg.perturbation == "none" or attempt == 1:
        return None
    return cfg.seed * 1000 + attempt


def schema_for(cfg, attempt: int) -> str:
    return schema_context.build(cfg.schema_mode, shuffle_seed=shuffle_seed(cfg, attempt))


def stability(rows) -> dict:
    """How each case fared across attempts — the noise floor, case by case.

    `verdict_changed` counts cases whose verdict moved at all; `pass_changed`
    counts the ones where it moved across the pass line, which is what a score
    comparison actually feels.
    """
    by_case: dict[str, list[str]] = {}
    for row in sorted(rows, key=lambda r: r.attempt):
        by_case.setdefault(row.case_id, []).append(row.verdict)

    cases = {
        case_id: {"passes": sum(v in PASSING for v in verdicts),
                  "attempts": len(verdicts), "verdicts": verdicts}
        for case_id, verdicts in by_case.items()
    }
    return {
        "verdict_changed": sum(len(set(c["verdicts"])) > 1 for c in cases.values()),
        "pass_changed": sum(0 < c["passes"] < c["attempts"] for c in cases.values()),
        "cases": cases,
    }


def build_prompt(cfg, case, schema_block: str) -> prompt.Prompt:
    with tracing.span("build_prompt", tracing.CHAIN, input_value=case.question) as span:
        built = prompt.build(cfg, case.question, schema_block)
        tracing.set_output(span, f"prompt {built.version}, {len(built.user):,} chars")
    return built


def generate_sql(cfg, built: prompt.Prompt, name: str = "generate_sql"):
    """One model call, plus reading the reply against the prompt contract."""
    with tracing.llm_span(cfg, built, name=name) as span:
        completion = llm.generate(cfg, built.user, system=built.system)
        tracing.record_completion(span, completion)
    return completion, prompt.parse(completion.text)


def execute_sql(con, sql: str, role: str = "agent"):
    """Run agent SQL through the same guarded path gold uses."""
    with tracing.span(f"execute_sql.{role}", tracing.TOOL, input_value=sql,
                      metadata={"role": role}) as span:
        result = db.run_sql(con, sql)
        tracing.add_metadata(span, rows=len(result), truncated=result.truncated)
        # The answer itself, not just its size: "NULL" or "117.0 vs 117.2" is
        # the fastest flag a person reading the trace gets.
        tracing.set_output(span, record.preview_text(record.preview(result)))
    return result


def generate_answer(cfg, question: str, head: dict):
    """The agent's prose answer, written from the result preview alone."""
    built = prompt.build_answer(cfg.prompt_version, question, record.preview_text(head))
    with tracing.llm_span(cfg, built, name="generate_answer") as span:
        completion = llm.generate(cfg, built.user, system=built.system)
        tracing.record_completion(span, completion)
    return completion.text.strip(), round(completion.elapsed_s, 2)


def run_agent_sql(con, cfg, built: prompt.Prompt, base: dict):
    """Run the agent's SQL; if it fails, show the model the error and retry,
    up to `cfg.sql_retries` times.

    Returns the result, the last exception, or the Reply if the model declined
    on a retry. `base` is updated in place: the SQL, reply and token counts
    become the final attempt's (tokens summed), and the first failed query and
    its error are kept, so the row shows both what was scored and what the
    retry fixed.
    """
    for tries in range(cfg.sql_retries + 1):
        try:
            return execute_sql(con, base["sql"], role="agent.retry" if tries else "agent")
        except db.QueryError as error:
            if tries == cfg.sql_retries:
                return error
            if tries == 0:
                base["first_sql"], base["first_error"] = base["sql"], str(error)[:500]
            base["retries"] = tries + 1
            follow_up = prompt.build_retry(built, base["sql"], str(error)[:500])
            try:
                completion, reply = generate_sql(cfg, follow_up, name="generate_sql.retry")
            except (llm.LLMError, prompt.PromptError) as failure:
                return failure
            base["raw_response"] = completion.text
            base["prompt_tokens"] += completion.prompt_tokens
            base["output_tokens"] += completion.output_tokens
            base["reasoning_tokens"] += completion.reasoning_tokens
            base["cached_tokens"] += completion.cached_tokens
            base["model_elapsed_s"] = round(base["model_elapsed_s"] + completion.elapsed_s, 2)
            base["done_reason"] = completion.done_reason
            if reply.refused:
                return reply
            base["sql"] = reply.sql
    raise AssertionError("unreachable")


def compare_rows(gold, agent, contract, resolver):
    with tracing.span("compare_rows", tracing.EVALUATOR) as span:
        gold_rows, agent_rows = resolve.resolve_pair(gold, agent, contract, resolver)
        outcome = comparator.compare(gold_rows, agent_rows, contract)
        tracing.set_output(span, f"{outcome.verdict}: {outcome.detail}".rstrip(": "))
    return outcome


def run_case(con, cfg, case, resolver, schema_block: str, attempt: int,
             run_id: str = "") -> record.CaseRow:
    """One case attempt, traced as one trace in the run's session."""
    with tracing.case_span(case, cfg, run_id, attempt) as span:
        row = _run_case(con, cfg, case, resolver, schema_block, attempt)
        tracing.finish_case(span, row)
        trace_id, span_id = tracing.ids(span)
    return dataclasses.replace(row, trace_id=trace_id, span_id=span_id)


def _run_case(con, cfg, case, resolver, schema_block: str, attempt: int) -> record.CaseRow:
    base = {
        "case_id": case.id, "tier": case.tier, "expects": case.expects,
        "attempt": attempt, "question": case.question,
    }

    built = build_prompt(cfg, case, schema_block)
    try:
        completion, reply = generate_sql(cfg, built)
    except (llm.LLMError, prompt.PromptError) as error:
        return record.CaseRow(
            **base, verdict=comparator.ERROR, detail=type(error).__name__,
            error=str(error)[:500],
        )

    base |= {
        "raw_response": completion.text,
        "prompt_tokens": completion.prompt_tokens,
        "output_tokens": completion.output_tokens,
        "reasoning_tokens": completion.reasoning_tokens,
        "cached_tokens": completion.cached_tokens,
        "done_reason": completion.done_reason,
        "model_elapsed_s": round(completion.elapsed_s, 2),
    }

    if reply.refused:
        verdict = REFUSAL_CORRECT if case.expects == "refusal" else REFUSAL_UNEXPECTED
        return record.CaseRow(**base, verdict=verdict, refusal=reply.refusal,
                              detail=reply.refusal or "")

    base["sql"] = reply.sql

    # A refusal case has no gold SQL to compare against, so the answer itself
    # is the finding. Still executed, because whether the invented query even
    # runs is the interesting part for error analysis.
    if case.expects == "refusal":
        detail, head = "", None
        try:
            got = execute_sql(con, reply.sql)
            detail, head = f"answered anyway, {len(got)} rows", record.preview(got)
        except db.QueryError as error:
            detail = f"answered anyway, query failed: {str(error)[:160]}"
        return record.CaseRow(**base, verdict=REFUSAL_ABSENT, detail=detail,
                              agent_preview=head)

    try:
        gold = execute_sql(con, case.gold_sql, role="gold")
    except db.QueryError as error:  # a broken gold case is ours, not the agent's
        return record.CaseRow(**base, verdict=comparator.ERROR,
                              detail="gold SQL failed", error=str(error)[:500])

    agent = run_agent_sql(con, cfg, built, base)
    if isinstance(agent, prompt.Reply):     # declined on the retry
        return record.CaseRow(**base, verdict=REFUSAL_UNEXPECTED, refusal=agent.refusal,
                              detail=agent.refusal or "", gold_rows=len(gold),
                              gold_preview=record.preview(gold))
    if isinstance(agent, Exception):
        failed = isinstance(agent, db.QueryError)
        return record.CaseRow(**base, verdict=comparator.ERROR, gold_rows=len(gold),
                              gold_preview=record.preview(gold),
                              detail="agent SQL failed" if failed else type(agent).__name__,
                              error=str(agent)[:500])

    head = record.preview(agent)
    if cfg.write_answer:
        try:
            base["answer"], base["answer_elapsed_s"] = generate_answer(cfg, case.question, head)
        except llm.LLMError as failure:   # no answer is a gap, not a wrong SQL verdict
            base["answer"] = None
            base["error"] = f"answer failed: {failure}"[:500]

    outcome = compare_rows(gold, agent, case.contract, resolver)
    return record.CaseRow(
        **base, verdict=outcome.verdict, detail=outcome.detail,
        gold_rows=len(gold), agent_rows=len(agent),
        gold_preview=record.preview(gold), agent_preview=head,
        query_elapsed_s=round(gold.elapsed_s + agent.elapsed_s, 3),
    )


def run_suite(cfg, selected=None, runs_dir: Path | None = None, verbose: bool = True,
              experiment: bool = False):
    """One pass over the gold set. With `experiment`, each attempt is also a
    fuse experiment over dataset `sref-gold` (see `tracing.experiment`)."""
    suite = case_loader.load_cases()
    if selected:
        wanted = set(selected)
        suite = [c for c in suite if c.id in wanted]
        missing = wanted - {c.id for c in suite}
        if missing:
            raise SystemExit(f"no such case: {', '.join(sorted(missing))}")
    if not suite:
        raise SystemExit("no cases to run")
    dataset_id = None
    if experiment:
        try:
            dataset_id = dataset.require(suite)
        except (RuntimeError, KeyError) as error:
            raise SystemExit(f"experiment: {error}") from error

    attempts = range(1, cfg.repeats + 1)
    schema_blocks = {a: schema_for(cfg, a) for a in attempts}
    schema_block = schema_blocks[1]

    # Fail on a misconfiguration now, not ten minutes in. Every attempt's block
    # is the same length, so checking the first checks them all.
    if verbose:
        window = f", num_ctx {cfg.num_ctx:,}" if cfg.provider == "ollama" else ""
        print(f"preflight: {cfg.provider} {cfg.model}, {cfg.schema_mode} schema{window}",
              file=sys.stderr, flush=True)
    probe = llm.preflight(cfg, schema_block + "\n\nReply with the single word OK.")
    if verbose:
        spare = (f", {cfg.num_ctx - probe.prompt_tokens:,} to spare"
                 if cfg.provider == "ollama" else "")
        print(f"  prompt read as {probe.prompt_tokens:,} tokens{spare} ({probe.elapsed_s:.0f}s)",
              file=sys.stderr, flush=True)

    con = db.connect()
    resolver = resolve.Resolver.from_snapshot(con)
    manifest = snapshot.load_manifest()

    run = record.RunRecord.start(
        cfg, case_count=len(suite) * cfg.repeats,
        snapshot_sha=manifest["sha256"], schema_block_chars=len(schema_block),
        schema_orders={str(a): schema_context.table_names(shuffle_seed=shuffle_seed(cfg, a))
                       for a in attempts},
        runs_dir=runs_dir,
    )
    if verbose:
        print(f"run {run.run_id} -> {run.directory}", file=sys.stderr, flush=True)

    if dataset_id:
        run.header["experiments"] = [tracing.experiment_name(run.run_id, a) for a in attempts]
    for attempt in attempts:
        if verbose and cfg.repeats > 1:
            print(f"attempt {attempt}/{cfg.repeats}", file=sys.stderr, flush=True)
        with (tracing.experiment(cfg, run.run_id, attempt, dataset_id) if dataset_id
              else nullcontext()):
            for case in suite:
                try:
                    row = run_case(con, cfg, case, resolver, schema_blocks[attempt], attempt,
                                   run_id=run.run_id)
                except Exception:  # noqa: BLE001 - one bad case must not end the run
                    row = record.CaseRow(
                        case_id=case.id, tier=case.tier, expects=case.expects,
                        attempt=attempt, question=case.question,
                        verdict=comparator.ERROR, detail="runner error",
                        error=traceback.format_exc()[-500:],
                    )
                run.append(row)
                if verbose:
                    mark = "PASS" if row.verdict in PASSING else "    "
                    print(f"  {mark} {row.verdict:<20} {row.case_id}",
                          file=sys.stderr, flush=True)

    summary = stability(run.rows)
    run.finish(stability=summary)
    if verbose:
        passed = sum(1 for r in run.rows if r.verdict in PASSING)
        print(f"\n{passed}/{len(run.rows)} passing   {run.header['verdicts']}",
              file=sys.stderr, flush=True)
        if cfg.repeats > 1:
            for case_id, c in summary["cases"].items():
                print(f"  {c['passes']}/{c['attempts']}  {case_id:<40} {' '.join(c['verdicts'])}",
                      file=sys.stderr, flush=True)
            print(f"verdict changed in {summary['verdict_changed']} of {len(summary['cases'])} "
                  f"cases; crossed the pass line in {summary['pass_changed']}",
                  file=sys.stderr, flush=True)
    return run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the gold set against a model.")
    parser.add_argument("--config", required=True,
                        help=f"run config name or path; have {config.available()}")
    parser.add_argument("--case", action="append", dest="cases",
                        help="run only this case id; repeatable")
    parser.add_argument("--runs-dir", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-trace", action="store_true",
                        help="send no traces, even to backends configured in .env")
    parser.add_argument("--experiment", action="store_true",
                        help="also record each attempt as a fuse experiment on dataset "
                             f"{dataset.DATASET_NAME}")
    args = parser.parse_args(argv)
    if args.experiment and args.no_trace:
        parser.error("--experiment needs traces; drop --no-trace")

    # Shell variables win over .env. Loaded here, not on import, so tests and
    # library use never pick up keys or send traces.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    cfg = config.load(args.config)
    if not args.no_trace:
        backends = tracing.configure(tracing.exporters_from_env())
        if args.experiment and "langfuse" not in backends:
            parser.error("--experiment needs fuse settings in .env")
        if not args.quiet:
            print(f"tracing to: {', '.join(backends) or 'nothing configured'} "
                  f"(environment: {record.environment()})", file=sys.stderr, flush=True)
    try:
        run = run_suite(cfg, selected=args.cases, runs_dir=args.runs_dir, verbose=not args.quiet,
                        experiment=args.experiment)
    finally:
        tracing.shutdown()
    if not args.no_trace and not args.quiet:
        print(f"grade the traces: uv run python -m evals.scores --run {run.run_id}",
              file=sys.stderr, flush=True)
    return 0 if all(r.verdict in PASSING for r in run.rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
