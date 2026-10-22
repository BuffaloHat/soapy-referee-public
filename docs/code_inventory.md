# SREF — Code Inventory
**Last updated:** 2026-10-20

> What each file is and why it exists. The PRD says what each phase must
> deliver. This doc describes what is here now.
>
> "Added in Phase N" is when a file first appeared, not what it is used by —
> `db.py` serves every phase after the one that created it.

| File | Added in |
|---|---|
| `agent/build_schema_docs.py` · `agent/schema_docs.yaml` | Phase 0 (discovery) |
| `evals/db.py` · `evals/comparator.py` · `evals/snapshot.py` · `evals/snapshot.yaml` | Phase 1 |
| `tests/test_db.py` · `tests/test_snapshot.py` · `tests/test_comparator.py` · `tests/test_gold_equivalence.py` | Phase 1 |
| `evals/cases.py` · `evals/cases/*.yaml` · `evals/resolve.py` | Phase 2 |
| `tests/test_cases.py` · `tests/test_resolve.py` | Phase 2 |
| `evals/config.py` · `evals/configs/*.yaml` · `agent/schema_context.py` | Phase 3 |
| `agent/ollama.py` · `agent/prompt.py` · `agent/prompts/*.md` | Phase 3 |
| `evals/runner.py` · `evals/record.py` | Phase 3 |
| `tests/test_config.py` · `tests/test_schema_context.py` · `tests/test_ollama.py` · `tests/test_prompt.py` · `tests/test_runner.py` | Phase 3 |
| `evals/tracing.py` · `tests/test_tracing.py` · `infra/` | Phase 4 |
| `evals/scores.py` · `tests/test_scores.py` | Phase 5 |
| `evals/judge.py` · `evals/judge_prompts/` · `evals/configs/judge/` · `tests/test_judge.py` | Phase 6 |
| `agent/llm.py` · `agent/completion.py` · `agent/openai_api.py` · `tests/test_openai_api.py` | Phase 7 |
| `evals/dataset.py` · `evals/api.py` · `tests/test_dataset.py` | Phase 7 |
| `evals/gate.py` · `evals/configs/gate/` · `tests/test_gate.py` | Phase 7 |
| `evals/metrics.py` · `evals/report.py` · `evals/configs/report/` · `docs/report/` · `tools/build_pdf.sh` · `tests/test_metrics.py` · `tests/test_report.py` | Phase 8 |

---

## `evals/` — the harness

### `db.py` — added in Phase 1
The single path to the snapshot. Gold SQL and agent SQL both run through it,
in the same run, against the same file.

- `connect()` — in-memory DuckDB with `data/nba.sqlite` attached read-only as
  `nba`. Verifies the snapshot on the way in, then sets
  `enable_external_access = false`, so a query cannot reach any other file.
- `guard(sql)` — DuckDB's own parser decides what the statement is: exactly
  one statement, and it must be a SELECT. Not pattern matching.
- `run_sql(con, sql, timeout_s, row_cap)` — runs the query and returns a
  `QueryResult` (columns, rows, `truncated`, `elapsed_s`).

Worth knowing:
- **DuckDB has no query timeout.** A timer thread calls `con.interrupt()`.
  The connection stays usable afterward, which matters because one connection
  serves a whole suite.
- **The row cap fetches one row past the cap** to set `truncated`.
- **PRAGMA is allowed.** DuckDB rewrites `PRAGMA x` into `SELECT * FROM
  pragma_x`, so it is not a distinct statement type. It is read-only and only
  exposes metadata.

### `comparator.py` — added in Phase 1
Classifies one result set against another. Values only: column names are
presentation and are never compared.

- `compare(gold_rows, agent_rows, contract)` → `Comparison(verdict, detail,
  matched_columns)`.
- Verdicts, strictest first: `exact`, `tolerance`, `unordered`, `superset`,
  `subset`, `wrong`, `error`. `superset` and `subset` are reported, never
  counted as correct.
- `Contract` holds the per-case settings: `ordered`, `decimals`,
  `coerce_numeric_strings`, `allow_extra_columns`.

Canonicalization: column order is searched, not assumed, so gold's columns are
found wherever the agent put them; extra agent columns are allowed by default;
`'30'` equals `30` (the snapshot's mixed-type columns); a midnight timestamp
equals a date; whitespace is collapsed; NULL never equals 0.

Worth knowing:
- **`exact` is tried before rounding**, so `tolerance` only applies when
  rounding is what made the two agree.
- **Result sets are bags, not sets.** A row that gold has twice and the agent
  has once is `subset`, not a match.
- **Column search is capped** at 7 agent columns (`MAX_PERMUTED_COLUMNS`);
  wider results fall back to row-wise comparison.

### `cases.py` — added in Phase 2
Loads and validates gold cases from `evals/cases/`. Malformed cases fail at
load time, not mid-run.

- `load_cases()` → `list[Case]`; `load_case(path)` → one; `CaseError` on
  anything malformed.
- A `Case` carries: `id`, `question`, `tier` (baseline · trap · hard ·
  refusal), `expects` (rows · refusal), `gold_sql`, `check_sql`,
  `cardinality`, `contract` (a comparator `Contract`), `traps`, `provenance`,
  `notes`.
- Validation: id matches the filename, known tier and expects values, known
  contract fields, answering cases carry `check_sql` and `cardinality`,
  refusal cases carry no SQL, no duplicate ids.

Worth knowing:
- **No expected rows are stored.** `gold_sql` runs at eval time against the
  same snapshot the agent queried.
- **`check_sql` is not optional.** A gold answer nobody cross-checked is a
  guess; `tests/test_cases.py` enforces that the two agree.

### `cases/*.yaml` — added in Phase 2
The gold set, one file per case so diffs stay readable. The case index, and
the rules for writing a case, live in [gold_cases.md](gold_cases.md).

### `resolve.py` — added in Phase 2
Maps teams, players, and seasons to one canonical form before comparing, so
the comparator judges the answer rather than its spelling.

- `Resolver.from_snapshot(con)` builds label → id maps from the snapshot's own
  columns: team labels (`game`, `team`, `team_history`) → `team_id`; player
  names (`player`, `common_player_info`) → player id.
- `season(value)` maps `'2007-08'`, `'2007-2008'`, `'22007'`, `'2007'` → `2007`.
- `resolve_pair(gold, agent, contract, resolver)` — gold's columns are resolved
  by name (the contract's `teams`, `players`, `seasons`); the agent's by
  shape, only for the kinds the contract declares.

Worth knowing:
- **A label resolves only if it names exactly one entity.** "Hornets" (two
  franchises) and "George Johnson" (three players) pass through unchanged.
- **Integers are never read as seasons.** `22007` could be an attendance.
- **Nothing resolves unless the contract asks** — tested.

### `config.py` — added in Phase 3
The settings of one run. Every variable that could change an answer lives
here, never in source, because a verdict only means something next to the
settings that produced it.

- `load(name_or_path)` → `RunConfig`, by name (`evals/configs/<name>.yaml`) or
  path. `from_dict(raw)` for an in-memory config. `available()` lists names.
- `RunConfig` holds `name`, `model`, `prompt_version`, `schema_mode`,
  `provider`, `temperature`, `seed`, `repeats`, `perturbation`,
  `max_output_tokens`, `num_ctx`, `sql_retries`, `write_answer`, `notes`.
- `to_header()` → the run record's header.
- `sql_retries` (default 0, added in Phase 5): how many times a failed agent
  query is shown back to the model with the database's error.
  `local-docs-r3-retry` is `local-docs-r3` with `sql_retries: 1`.
- `write_answer` (default false, added in Phase 6): a second call writes a
  prose answer from the rows. `local-docs-r3-answer` turns it on.

Worth knowing:
- **Unknown fields are rejected.** A typo'd setting would otherwise be dropped
  silently while the run record claimed it was applied.
- **`to_header()` round-trips** back through `from_dict` — tested. The run
  record has to be enough to rebuild the run.
- **Endpoints and secrets are not run variables.** `ollama_host` reads
  `OLLAMA_HOST` and is excluded from the header: two machines pointing at
  different hosts are running the same run.
- **`num_ctx` defaults to 32,768.** Ollama drops prompt overflow without
  erroring, so too small a window is a confidently wrong answer and no log
  line. A test asserts every shipped config leaves 50% headroom over the
  largest rendered schema block.
- **`repeats` above 1 at temperature 0 needs a `perturbation`.** Greedy
  decoding over an identical prompt replays one answer, so k identical
  attempts would read as stability without measuring any. Rejected at load.
  `perturbation: table_order` shuffles the schema block's table order per
  attempt — same facts, different prompt.

### `configs/*.yaml` — added in Phase 3
One file per run configuration. `local-columns` and `local-docs` are the two
arms of the schema-context experiment; a test asserts they differ only in
`name`, `schema_mode`, and `notes`, so any verdict delta is attributable.
`local-columns-r3` and `local-docs-r3` are the noise-floor versions: three
attempts with `table_order`, and otherwise identical to their base arm, so
attempt 1 is the same prompt as a single-attempt run.

### `runner.py` — added in Phase 3
Runs the gold set against one config and writes down what happened.
`uv run python -m evals.runner --config local-docs`.

- `run_suite(cfg, selected, runs_dir, experiment)` — preflights, opens one
  connection, runs every case for every attempt, returns the `RunRecord`.
- `--experiment` (Phase 7) also records each attempt as a fuse experiment on
  dataset `sref-gold`, named `<run_id> #N`; the names go in the run header as
  `experiments`. Needs tracing and fuse settings, and checks every case has a
  dataset item before the run starts.
- `schema_for(cfg, attempt)` — the schema block for one attempt.
  `shuffle_seed(cfg, attempt)` is `None` for attempt 1 and for unperturbed
  configs, so attempt 1 is always catalog order.
- `stability(rows)` — per case, passes over attempts and the verdict
  sequence; overall, `verdict_changed` (any movement) and `pass_changed`
  (movement across the pass line — what a score comparison feels).
- `run_case(...)` → one `CaseRow`. Steps are named and thin —
  `build_prompt`, `generate_sql`, `execute_sql`, `compare_rows` — so Phase 4
  wraps each in a span instead of rewriting this file.
- `run_agent_sql(...)` (Phase 5) — runs the agent's SQL and, on failure,
  retries up to `sql_retries` times through `generate_sql.retry`. The row
  keeps the final SQL plus `retries`, `first_sql`, `first_error`; token counts
  sum both calls.
- `generate_answer(cfg, question, head)` (Phase 6) — the prose answer, from
  the question and the agent's result preview only; traced as
  `generate_answer`. Stored as `answer` on the row, never part of the SQL
  verdict.
- CLI: `--config`, `--case` (repeatable), `--runs-dir`, `--quiet`,
  `--experiment`, `--no-trace`. Exit 1 if anything failed.

Worth knowing:
- **Refusals get their own outcomes**, not the comparator's vocabulary, since
  there are no rows to compare: `refusal_correct` (asked to decline,
  declined), `refusal_absent` (should have declined, answered anyway),
  `refusal_unexpected` (should have answered, declined). A refusal case's
  invented query is still executed — whether it even runs is the finding.
- **One pass per config, not per case.** The schema block is a constant
  prefix and Ollama caches it: on the `docs` arm the first call costs ~149s
  and the median is 6.2s. Interleaving configs would pay the cold cost every
  time. Attempts are the outer loop for the same reason: a perturbed run pays
  one cold call per attempt.
- **A broken gold case is scored `error`, not against the agent.**
- **One bad case cannot end a run.** An unexpected exception becomes an
  `error` row carrying the traceback.

### `record.py` — added in Phase 3
The run record: the harness's own artifact, and what everything downstream
reads. Written to `runs/<run_id>/` as `header.json` plus `cases.jsonl`.

- `RunRecord.start(...)` → writes the header immediately. `append(row)` →
  one JSON line per case per attempt. `finish(stability)` → stamps the end,
  tallies verdicts, and stores the runner's stability summary.
- `load(directory)` → `(header, rows)`. `latest()` → the newest run.
- `git_commit()` → the short hash, with `-dirty` appended when the working
  tree has uncommitted changes; `git_dirty()` → the flag alone. Both go in the
  header, and the label is the trace release.
- `environment()` → the run label from `SREF_ENV` (default `dev`), validated
  against Langfuse's naming rules, the strictest of the three tools. Written
  to the header and used as the trace environment.
- `CaseRow` carries verdict, detail, the SQL, the raw reply verbatim, prompt
  and output token counts, `done_reason`, row counts, timings, any error, and
  the trace's `trace_id` and root `span_id` (empty when untraced).
- `gold_preview` / `agent_preview` on each row: columns, the first 5 rows and
  the total, from `preview(result)`. `preview_text()` renders one as a small
  text table, which is also the `execute_sql` steps' trace output. Added in
  Phase 5 so a person sees "NULL vs 117.2" without re-running anything.

Worth knowing:
- **Written incrementally.** A `docs` run takes minutes; a crash at case eight
  keeps cases one to seven, and a run in progress is readable while it runs.
- **The header carries the snapshot SHA and the git commit.** A verdict means
  nothing without the data and the code behind it, and `config` round-trips
  back through `config.from_dict`. `schema_orders` lists the table order each
  attempt saw, so a perturbed prompt can be rebuilt.
- **Flat on purpose** — no nesting; that is the traces' job. The trace ids
  were added in Phase 5 as join keys: scores go out by them, labels come back
  by them.
- `runs/` is gitignored, except the four runs the report and the gate use.

### `tracing.py` — added in Phase 4
Sends each run's traces to Phoenix, Langfuse and LangSmith at once: one
OpenTelemetry provider, one OTLP exporter per backend, OpenInference
attribute names that all three read.

- `exporters_from_env()` — a backend is on when its settings are in `.env`.
  `configure(exporters)` starts tracing; `shutdown()` flushes and goes back to
  no-op. The runner's CLI calls both; `--no-trace` skips them.
- `case_span` / `finish_case` — the root of one case attempt's trace.
  `llm_span` / `record_completion` — model, settings, messages, tokens.
  `span(name, kind, metadata=…)` — everything else; `add_metadata` merges keys
  into a span after the fact.
- Every span inherits the case's join keys (`run_id`, `case_id`, `attempt`,
  `config`, `session_id`) through a context variable.

Worth knowing:
- **One trace per case attempt, one session per run.** `session.id` is the
  `run_id`; metadata carries `case_id`, `attempt` and the verdict — the join
  key back to the run record. A run-sized trace would sit open for minutes.
- **No-op until configured.** Tests and library use never send anything.
- **Tool-specific attributes sit next to the shared ones,** each commented
  with its lesson number in observability_tools.md §6: `metadata.session_id`
  for smith's threads, `langsmith.span.tags`, `langfuse.observation.metadata.*`,
  `langfuse.release` / `.version`, and an exception event on a failed case's
  root so smith flags it. The LLM span's `input.value` is the system + user
  message list as JSON, because smith and fuse display that field.
- **Environment and release** go on the resource: `deployment.environment.name`
  from `SREF_ENV` (default `dev`) and `service.version` = the git commit.
- **Each tool reads the same attributes a little differently.** smith drops
  unrecognised attributes and the system prompt; fuse keeps `metadata` as a
  raw string; phx shows everything as sent. Field by field in
  [observability_tools.md](observability_tools.md) §6.

### `scores.py` — added in Phase 5
Sends each case attempt's verdict to the tools as their native grade — smith
feedback, fuse score, phx annotation — on the root of its trace. Read from the
run record after the run: `uv run python -m evals.scores [--run <run_id>]`.

- Two grades per attempt in every tool: `verdict` (categorical) and `pass`
  (boolean, the runner's `PASSING` line). The detail goes as the comment.
- One builder per tool (`smith_feedback`, `fuse_score`, `phx_annotation`) and
  one sender per tool; `senders_from_env()` uses the same `.env` settings as
  tracing. One tool failing does not stop the others.

Worth knowing:
- **smith ids are the OTel span id zero-padded into a UUID**
  (`15d103ff9f2dfc5d` → `00000000-0000-0000-15d1-03ff9f2dfc5d`), read back
  from its API. A root run's id is also its trace id.
- **Re-sending is safe.** Each grade's id is built from its span and name, so
  every tool updates in place: fuse upserts by id, phx by (name, span), smith
  answers 409 and the grade is patched.
- **No `extend_trace_retention`,** so graded smith traces stay on the free
  14-day tier.
- fuse scores carry the run's environment; without it they land in `default`.
- **fuse scores sit on the root step** (`observationId`), since Phase 7: where
  hand labels sit, and where fuse reads an experiment item's scores. Before
  that they were trace-level.
- Rows without trace ids (untraced runs, a runner crash) are skipped.

### `judge.py` — added in Phase 6
The faithfulness judge: does the agent's prose say only what its rows say?
`uv run python -m evals.judge --run <run_id> [--send]`.

- `JudgeConfig` from `evals/configs/judge/<name>.yaml` — the judge's own
  model and settings; `faithfulness-gemma` is `gemma4:12b` at temperature 0.
- `build(...)` — the prompt from `evals/judge_prompts/faithfulness/<v>.md`
  plus the question, the result preview and the answer. Never gold.
- `parse(text)` → `Judgement(faithful, reason, raw)`; `faithful` is `None`
  when the reply has no verdict.
- `agreement(pairs)` — judge against human: agree, lenient (passed what the
  human failed), strict, unreadable.
- Reads the run's answers plus `judge_set.jsonl` (the human-labelled set,
  with any planted answers), writes `judgements.jsonl`; `--send` adds
  `faithful_judge` / `faithful_human` scores in Langfuse.

Worth knowing:
- **The token cap includes hidden reasoning.** gemma thinks before its
  two-line reply; at 1,024 tokens a hard item came back empty.
- **Unreadable is not a pass.** It is counted on its own.

### `api.py` — added in Phase 7
`http()` for the tools' REST APIs and `fuse()` for fuse's URL and auth header.
Moved out of `scores.py` so `dataset.py` can use it without importing
`scores`, which imports the runner, which imports `dataset`.

### `dataset.py` — added in Phase 7
Copies the gold cases into fuse dataset `sref-gold`, one item per case, keyed
by case id so a re-sync updates in place. `uv run python -m evals.dataset`.

Worth knowing:
- **Questions only, no expected output.** Gold is executed, not stored. In
  fuse v4 each experiment trace carries its own expected output, so the gold
  answer is attached at run time from the gold SQL just run.
- **Items with no case are reported, not deleted.** Retiring a case is a manual
  decision.
- `require(suite)` — the dataset's fuse id, or an error naming the cases with
  no item. The runner calls it for `--experiment`.

### `gate.py` — added in Phase 7
The regression gate. Compares a run with a pinned baseline run, from the run
record alone, and exits 1 if either rule fails:
`uv run python -m evals.gate [--run <id>] [--baseline <id>] [--gate default]`.

1. **Score line** — pass rate over all attempts no more than `margin_points`
   below the baseline's.
2. **No lost stable case** — a case that passed every baseline attempt must
   not fail every attempt now.

Worth knowing:
- **The margin is the noise allowance.** `configs/gate/default.yaml` sets 10
  points (3 of 30 attempts), from sol crossing the pass line on 3 of 10 cases
  between attempts with nothing changed.
- **Baselines are pinned per config** in the gate config. Both point to
  runs included in `runs/`.
- **Refuses to judge** (exit 2) across snapshots, case sets or attempt counts,
  or a run against itself.
- Writes `gate.json` to the run folder, with the settings that changed from
  the baseline: the change under test.

### `metrics.py` — added in Phase 8
The eval's metrics, one definition each — the code behind
[eval_plan.md](eval_plan.md) §7. `summarize(rows, price)` returns pass rate
(strict and lenient), silent and loud failures, pass^k and pass@k, verdict
stability, median model time and cost for one run.

Worth knowing:
- **Silent** = answered without error and failed: `wrong`, `subset`,
  `superset`, `unordered`, `refusal_absent`. **Loud** = `error` or
  `refusal_unexpected`.
- **Cost prices cached input at its own rate;** prices come from the report
  config, not the run record. None for a local model.

### `report.py` — added in Phase 8
Writes the eval report: the hand-written narrative in
`docs/report/sref_eval_template.md`, with every table of numbers filled in from the
run record at `{{placeholders}}`. `uv run python -m evals.report` writes
`docs/sref_eval_report.md`; `tools/build_pdf.sh` turns it into the PDF.

Worth knowing:
- **Edit the template, not the output.** The output is overwritten on every
  run.
- **Which runs, and the prices,** are in `configs/report/default.yaml`. The
  four runs it names are included in `runs/`.
- **An unknown placeholder stops the build,** so a typo can't ship as
  literal `{{text}}`.

### `snapshot.py` — added in Phase 1
The frozen-snapshot fingerprint. Gold answers are only valid against the exact
file they were written for, so drift has to fail loudly.

- `check_file(path)` — size and SHA-256 against the manifest. Takes ~0.8s and
  runs on every `connect()`. No database needed.
- `check_tables(con)` — row counts for all 16 tables against the manifest.
- `build_manifest(con, path)` — regenerates the manifest. Only for a
  deliberate snapshot change.
- Raises `SnapshotError`.

### `snapshot.yaml` — added in Phase 1
The committed fingerprint: source, SHA-256, byte size, and row counts for all
16 tables. Generated by `snapshot.py`; edited only when the snapshot itself
changes on purpose.

---

## `agent/` — the system under test

Subject to the no-leak rule: nothing from the trap catalog, gold SQL, or eval
design belongs in this folder.

### `build_schema_docs.py` — added in Phase 0 (data discovery)
Generates `schema_docs.yaml` from the snapshot. Stats and samples are
regenerated on every run; hand-written descriptions are preserved.

- One scan per table for row count, null count, distinct count, min and max
  per column.
- Observed values branch on cardinality. At or below 12 distinct, every value
  is listed, read from the full column so `values_complete` cannot be a lie.
  Above that, min and max come from the full scan and a median plus one
  seeded draw come from a 10,000-row reservoir.
- Runs in ~12s. Opens its own DuckDB connection rather than going through
  `evals/db.py`.

Worth knowing:
- **The earlier sampler sorted a reservoir and took the first three rows**, so
  every column showed its low end only. `season_id` came back as three
  preseason codes with no sign that regular-season games are encoded
  differently — the treatment arm would have hidden the very encoding the
  model gets wrong. Min and max now span the range.
- **Min and max are full-column, not sampled.** A range taken from a sample is
  a narrower range stated as fact.

### `schema_docs.yaml` — added in Phase 0 (data discovery)
The agent-facing column catalog: 16 tables, 341 columns, each with type, null
%, distinct count, observed values, and a hand-written `description`.
Descriptions state what a column contains, never how to query it.

As of 2026-09-22: 16/16 table descriptions (each states grain) and 37/341
column descriptions. Most columns stay blank on purpose — a column earns a
description only when its meaning is not recoverable from its name plus the
values already listed, and only when another column in the same row can
contradict the reading.

### `llm.py` — added in Phase 7
The runner's one door to a model. `generate` and `preflight` route by the
config's `provider` (`ollama` or `openai`), so swapping qwen for an OpenAI
model is a config change. The judge still calls `ollama.py` directly.

### `completion.py` — added in Phase 7
What every provider returns and raises: `Completion`, `LLMError`,
`ContextOverflowError`. Moved out of `ollama.py` so both clients share them;
`ollama.py` re-exports them for older callers.

### `openai_api.py` — added in Phase 7
The comparison arm's client: Chat Completions over the standard library, the
same contract as `ollama.py`. Key from `OPENAI_API_KEY` in `.env`.

Worth knowing:
- **Reasoning models fix `temperature` at 1.** Anything else is rejected, so
  the config says 1 and the header records what actually ran.
- **`max_completion_tokens` covers hidden reasoning and the answer.** A small
  cap can go entirely to reasoning and return empty text with `done_reason`
  `length`; `reasoning_tokens` is recorded so that case can be read.
- **`cached_tokens`** is the part of the prompt OpenAI served from its cache,
  billed at a twentieth of the input price for `gpt-6.1-sol`. It is in the run
  record and on the LLM span; cost is wrong without it.
- **No truncate flag needed.** OpenAI refuses an oversized prompt itself;
  `context_length_exceeded` becomes `ContextOverflowError`.
- **Rate limits and 5xx are retried** (three waits, up to 30s) so the network
  never lands in the record as an error verdict. Out of credit is not retried.
- **`preflight`** checks the key can see the model, then sends the schema
  prompt once.

### `ollama.py` — added in Phase 3
Talks to the local model, and refuses to guess when the prompt will not fit.
Standard library only; no new dependency.

- `generate(config, prompt, system)` → `Completion` (text, `prompt_tokens`,
  `output_tokens`, `elapsed_s`, `done_reason`). `Completion` and the errors
  live in `completion.py` since Phase 7.
- `preflight(config, prompt)` — checks `num_ctx` against the model's real
  context length, then proves the prompt fits, before a run burns ten minutes.
- `model_context_length()`, `available_models()`.

Worth knowing:
- **Every call sends `truncate: false`.** Ollama's default on an oversized
  prompt is to drop tokens from the *front* and answer anyway — the schema
  block falls off, the question survives, and the model answers from general
  knowledge. With this flag the server returns HTTP 400 instead, raised as
  `ContextOverflowError`. Verified against a real server; an integration test
  will catch a future Ollama reverting to truncation.
- **`prompt_tokens` is trustworthy.** Cold and cached calls both report the
  full prompt count — caching changes latency, not the number — so it is a
  true record of what the model read.
- **`done_reason == "length"`** surfaces as `output_truncated`: the
  output-side mirror of the same problem.

### `prompt.py` — added in Phase 3
The prompt contract, both directions. Building and parsing live together
because the reply format is defined by the prompt text, and splitting them is
how a parser ends up looking for a format nobody asks for any more.

- `load(version)` → the system prompt from `agent/prompts/<version>.md`.
  Missing means stop, never a silent default that would credit a run to a
  prompt it never used.
- `build(config, question, schema_block)` → `Prompt(system, user, version)`.
- `build_retry(built, sql, error)` (Phase 5) → the same prompt plus the
  failed query and the database's error, from
  `agent/prompts/retry/<version>.md`. Nothing else is added, and the leak
  tests cover the retry text too.
- `build_answer(version, question, result_text)` (Phase 6) → the answer
  call from `agent/prompts/answer/<version>.md`: the question and the rows,
  no schema and no SQL, so a faithful answer can only come from the rows.
- `parse(text)` → `Reply(sql, refusal, raw)`. `REFUSAL_MARKER` is the string
  the prompt asks for and the parser looks for; a test asserts they match.

Worth knowing:
- **Fences are stripped, not rejected.** A model wrapping SQL in ```sql has
  answered and formatted it badly; failing that would measure
  instruction-following rather than text-to-SQL.
- **Only the opening line can be a refusal.** The first run found the parser
  scanning the whole reply, so a query carrying `-- cannot answer precisely`
  in a comment would have scored as a decline.
- **The raw reply is always kept**, so the record holds what the model said,
  not a parsed reading of it.

### `prompts/*.md` — added in Phase 3
One file per prompt version, selected by the config's `prompt_version`, so a
prompt change is a run setting rather than an edit to source. `v1` states the
dialect, the one-SELECT output contract, and the refusal line. It names no
table or column — a test enforces that, because a table name here would be a
hint that survives into the `columns` arm and contaminates the control.

### `schema_context.py` — added in Phase 3
Renders the schema block the agent sees, in the mode the config selects.

- `build(mode)` → the block. `columns` is `CREATE TABLE` DDL, names and types
  only, ~2.2k tokens — the standard text-to-SQL baseline and the control arm.
  `docs` adds row counts, coverage, distinct counts, observed values and the
  hand-written descriptions, ~11.6k tokens.
- `build(mode, shuffle_seed=n)` → the same block with whole tables in a
  seeded order. Columns never move, and no description refers to another
  table by position (checked 2026-10-01), so every order states the same
  facts. `table_names(shuffle_seed=n)` → tables in rendered order.

Worth knowing:
- **A complete value set and a spread across a range are labelled
  differently** — `values 'L', 'W'` versus `range '12005' to '42022' · e.g.
  …`. An unlabelled list of three reads as "typical" whichever it is, which is
  how the old sampler misled.
- **The arms differ 5× in size.** A `docs` win is partly a longer-prompt win;
  separating the two needs a third arm.

---

## `infra/` — observability backends

### `docker-compose.yml` — added in Phase 4
Phoenix and a self-hosted Langfuse in one compose project, `infra_sref`.
`cd infra && docker compose up -d`. LangSmith is cloud-only and not here.

Worth knowing:
- **Trimmed from Langfuse's upstream compose (v4).** Same services — web,
  worker, Postgres, ClickHouse, Redis, MinIO — minus settings that default off.
- **Secrets have no fallbacks.** They come from gitignored `infra/.env`
  (template: `infra/.env.example`); compose refuses to start without them.
- **Headless init** creates the Langfuse user, org, `soapy-referee` project
  and API keys on first start, so the keys are known before the UI is opened.
- **Ports:** only Phoenix (6006 UI + OTLP HTTP, 4317 gRPC) and Langfuse
  (3100, not its default 3000) are published, on localhost; the databases
  are internal to the network.
- **Langfuse v4 retired the v1 traces read API** — reading back uses
  `/api/public/v2/observations`. Ingestion is OTLP HTTP at `/api/public/otel`.

---

## `tools/` and `docs/report/` — the PDF pipeline

### `tools/build_pdf.sh` — added in Phase 8
Turns one Markdown doc into a PDF: pandoc renders self-contained HTML with
images inlined, then headless Chrome prints it. No PDF engine needed.
`tools/build_pdf.sh docs/sref_eval_report.md docs/sref_eval_report.pdf`.
Builds both the eval report and the Technical Reference.

### `docs/report/` — added in Phase 8
- `sref_eval_template.md` — the eval report's hand-written narrative with
  `{{placeholders}}`; `evals/report.py` fills them from the run record.
- `report.css` — the print stylesheet for both PDFs: page size and margins,
  typography, tables, figures. Tables and figures never split across a page;
  headings stay with the text that follows.

---

## `tests/`

Run with `uv run pytest`.

### `test_db.py` — added in Phase 1
Row cap and truncation, timeout enforcement, connection reuse after a timeout,
rejected statements (DDL, DML, `ATTACH`, `COPY`, multiple statements, invalid
SQL), CTEs and leading comments accepted, read-only enforcement, and blocked
filesystem access.

### `test_snapshot.py` — added in Phase 1
The real file against the manifest, plus failure cases: missing file, wrong
checksum, wrong size, wrong row count.

### `test_comparator.py` — added in Phase 1
25 unit tests, no database: every verdict, column reordering, extra and
missing columns, rounding vs. real numeric difference, numeric strings,
timestamps, NULLs, duplicate rows, and both sides empty. Two cases are real
failures from the qwen speed test — an empty result from the wrong season
format (`subset`) and the home-only Lakers average, 117.0 vs. 117.2 (`wrong`).

### `test_cases.py` — added in Phase 2
Every case proves itself: gold SQL runs, returns the cardinality it claims,
and comes back `exact` against `check_sql`. Plus loader rejections — unknown
tier or expects, missing `check_sql` or cardinality, unknown contract field,
id/filename mismatch, and a refusal case carrying SQL.

### `test_resolve.py` — added in Phase 2
Team labels, player names, and season forms resolve; ambiguous or unknown
labels pass through; names plus season strings match gold ids `exact`; a wrong
team is still `wrong` after resolution; nothing resolves without the contract.

### `test_config.py` — added in Phase 3
Malformed configs fail at load time: missing or unknown fields, bad
`schema_mode`, out-of-range `temperature`, `repeats` or `max_output_tokens`
below one, missing file, unknown `perturbation`, and `repeats` above 1 at
temperature 0 with no perturbation. Plus the header round-trip, `ollama_host`
coming from the environment and staying out of the header, the two shipped
arms differing only in `name`, `schema_mode`, and `notes`, and each `-r3` arm
differing from its base only in attempts.

### `test_schema_context.py` — added in Phase 3
Both modes render every table; `columns` carries no stats or values; `docs`
labels a complete value set differently from a range; the empty table is
reported honestly; `num_ctx` leaves 50% headroom over the largest block. A
shuffle moves whole table blocks and changes nothing else, is reproducible
from its seed, and `table_names` follows the rendered order.

Also the Phase 3 leak tests: no instructional language in either block (19
phrases — "careful", "instead of", "note that" and their kin), no gold-SQL
clause fragment, no case question. Folder separation keeps eval material out
of `agent/`; these assert it stayed out.

### `test_ollama.py` — added in Phase 3
13 tests. Eleven fake the wire and run anywhere: the `truncate: false` flag is
actually sent, overflow raises its own error, other HTTP failures are not
mistaken for overflow, an unreachable server says so, run settings reach the
request, and preflight rejects both a `num_ctx` the model cannot honour and a
prompt that fills the window. Two integration tests skip without a server —
one asserts a real Ollama still refuses an oversized prompt, the other that
`prompt_tokens` survives caching.

### `test_openai_api.py` — added in Phase 7
15 tests, wire faked, no key or cost. Settings reach the request
(`max_completion_tokens`, not `max_tokens`); a missing key fails before any
call; reasoning tokens and a cap spent on reasoning are visible; overflow and
a rejected setting are told apart; rate limits are waited out but out of
credit is not; preflight names a model the key cannot use; the router and
config agree on the providers.

### `test_prompt.py` — added in Phase 3
Versions resolve or stop the run; every shipped config names a real prompt;
the refusal marker in the prompt is the one the parser looks for. Parsing:
plain SQL, fenced SQL, multi-line SQL, refusals through varied formatting, a
refusal with no reason, an empty reply as an error rather than a decline, and
the marker inside a query comment *not* read as a refusal. Plus leak tests —
no gold SQL, no case question, and no table name in any prompt version.

### `test_runner.py` — added in Phase 3
33 tests with the model faked and everything downstream real. Verdicts: a
`check_sql` answer scores `exact`, a wrong answer does not pass, broken or
rejected SQL is `error` rather than a wrong answer, an unreachable model is an
error. The full refusal 2×2. What lands in the record: the raw reply verbatim
alongside the cleaned SQL, token counts, `done_reason`. And the record itself
— written incrementally and readable mid-run, header carrying the snapshot SHA
and a round-trippable config, one JSON line per row with newlines escaped,
`latest()`, and one exploding case not ending the run. Attempts: attempt 1
is the unperturbed prompt, later attempts each get their own order, a verdict
wobble between passing verdicts is told apart from a pass flip, and a repeated
run records every attempt's table order and its stability summary.
Retries: none unless configured; a retry can fix a failed query; one that
fails again is still `error`; declining on the retry is
`refusal_unexpected`. Answers: written from the agent's rows, only when
configured and the SQL ran; the row shows both result previews, and a failed
agent query still shows gold's. The commit label carries `-dirty` when the
tree has uncommitted changes.

### `test_tracing.py` — added in Phase 4
Spans go to an in-memory exporter. One case is one trace with the five steps
beneath it in order; the root carries `session.id` and the join keys; the LLM
span carries model, tokens and messages; failing agent SQL is recorded on its
span and marks the root as an error; nothing is traced until configured; a
backend switches on only when all its settings are present; the run-record
row carries the root's trace and span ids, and none when untraced.

### `test_scores.py` — added in Phase 5
Requests go to a fake that records them. `pass` follows the runner's pass
line; the smith id mapping is pinned to an id read back from its API; each
tool's payload has the agreed shape; a grade's id is stable across sends; a
re-sent smith grade is patched, not duplicated; untraced rows are skipped; one
tool failing does not stop the others.

### `test_judge.py` — added in Phase 6
12 tests, no model called: replies are given as text. The first line decides
the verdict, through markdown, mixed case and an echoed label; a reply with
no verdict is unreadable, not a pass; `UNFAITHFUL` is never read as
`FAITHFUL`. The judge sees the question, the rows and the answer only, and
never gold. Agreement counts which way the judge errs: lenient, strict,
unreadable.

### `test_dataset.py` — added in Phase 7
8 tests, fuse faked. One item per case keyed by case id; an item holds the
question and never the gold SQL or an expected output; the dataset is created
before its items; items with no case are reported, not deleted; one failed
item does not stop the rest. For `--experiment`: every case must have an
item, the dataset's fuse id comes back, and a missing dataset says how to
make it.

### `test_gate.py` — added in Phase 7
12 tests on synthetic run folders. The same result passes; one lost attempt
is noise; a drop below the line fails; a stable case lost 3/3 → 0/3 fails
even when the score holds, while 3/3 → 2/3 does not; snapshots, case sets and
attempt counts must match; the changed settings are reported; the CLI exits
1 and writes `gate.json`; the shipped config loads with both baselines.

### `test_metrics.py` — added in Phase 8
9 tests. What passes; silent vs loud failures, including both refusal
mistakes; lenient adds only `unordered` and `superset`; pass^k and pass@k are
per case; stability counts; cached input priced at its own rate; a local
model has no cost.

### `test_report.py` — added in Phase 8
6 tests. Placeholders fill; an unknown one stops the build; the shipped
template uses only known placeholders; the grid shows each attempt and the
passes; a missing run is named; the metrics table reads the run record.

### `test_gold_equivalence.py` — added in Phase 1
The Phase 1 done-when test, against the real snapshot. "Best home record in
1996-97" written two ways — from `wl_home` and from the scores — returns
`exact`, and the top row matches the published record (CHI 39-2). A third test
pins down a path that *looks* equivalent and is not: joining `line_score`
covers 1,034 of the season's 1,189 games and silently changes the answer.
