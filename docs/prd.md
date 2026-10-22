# Soapy Referee (SREF) — PRD
**Last updated:** 2026-10-21

> The build path in phases, each with its goal, status and done-when
> criteria.

**Related:** [overview.md](overview.md) (what and why) · [code_inventory.md](code_inventory.md) (what each file is)

---

## Phase 0 — Foundation
**Status:** done (2026-09-17)
**Goal:** A clean project to build on.
- Repo, uv environment, frozen snapshot, data dictionary
- Root docs: overview, this PRD
- Data discovery: explore the snapshot hands-on in DBeaver (DuckDB connection,
  read-only) until the tables, grain, and traps feel familiar

**Done when:** docs are committed, the environment runs, and the data has
been explored hands-on.

---

## Phase 1 — DB Layer + Comparator
**Status:** done (2026-09-17)
**Goal:** Build the measuring instrument before anything it measures.
- `evals/db.py` — read-only DuckDB `ATTACH`, statement guard, timeout, row cap
- Snapshot checksum check that fails loudly if the data changes
- `evals/comparator.py` — canonicalize two result sets and classify a verdict
- Unit tests for the comparator

**Done when:** comparator tests pass, and the same question written as two
different queries comes back `exact`.

---

## Phase 2 — Gold Set, First Cases
**Status:** done (2026-09-21) — 10 cases, each reviewed by hand. Phases 5–7 kept
the same 10 rather than growing the set
**Goal:** Define what a test case is, and prove the format with a few real ones.
- Case format: question, gold SQL, comparison contract, expected behavior
- A small first batch, checked against published records

**Done when:** each case runs through the DB layer and comparator cleanly.

---

## Phase 3 — Agent + Runner
**Status:** done-when met 2026-09-22 — both arms run end to end. The prose
answer was deferred until something read it, and added in Phase 6.
**Goal:** Something to measure, and the loop that measures it.
- `agent/` — schema context → prompt → SQL → execute → prose answer
- `evals/runner.py` — runs gold and agent SQL, records verdicts
- Run settings as config (model, temperature, prompt version, schema-context mode)
- Run record: a run header (config) plus one flat row per case — prompt, raw
  response, SQL, verdict, latency, tokens. Flat only; no span ids or nesting.
- Agent steps as thin named functions (`build_prompt`, `generate_sql`,
  `execute_sql`, `compare`) taking and returning plain data

**Why the run record is here and not in Phase 4:** the harness is the product,
so the durable artifact has to belong to the harness — committable, diffable, queryable
across runs. Phoenix, Langfuse, and LangSmith are viewers: ephemeral and tool-specific.
Traces are the debugging lens over the run record, not a replacement for it.
Keeping the steps span-shaped means Phase 4 decorates them instead of
refactoring them.

**Done when:** one command runs the whole gold set against the local model
and produces verdicts.

---

## Phase 4 — Observability
**Status:** done (2026-10-05) — tracing to all three tools, walkthrough and
comparison in observability_tools.md, wiring fixes verified on a re-run,
findings article 7
**Goal:** See every run as traces, and compare three tools on the same ones.
- OpenTelemetry + OpenInference spans around the Phase 3 agent steps,
  carrying `run_id`, `case_id`, and `attempt` as the join key to the run record
- One instrumentation layer, one exporter per backend: Arize Phoenix (local),
  Langfuse (self-hosted, Docker), LangSmith (cloud free tier)
- Traces only. Verdicts as each tool's native scores or feedback wait for
  Phases 5–6, where labelling is the work
- The run record stays the source of truth; traces are the lens over it
- Tool walkthrough: the same 2–3 cases (one exact, one silently wrong, one
  agent-SQL error) followed through each tool's views — trace list, trace
  tree, LLM span, sessions, built-in metrics — field by field, from
  screenshots, tying each field to the span attribute behind it
- A findings write-up comparing the three tools on identical traces, written
  during the walkthrough
- Scope is traces. Scores, annotation queues, datasets and experiments are
  noted, not explored — they belong to Phases 5–6

**Done when:** one full run is inspectable in all three tools, walked through
view by view, with the comparison written up.

---

## Approach for Phases 5–9

Phases 5–7 each expose one major part of an eval (error analysis, an
eval-driven fix, an LLM judge, experiments, a regression gate) in a single
focused pass on the existing 10-case gold set. Phase 8 puts the results in
one deliverable; Phase 9 closes the project. An AI coding assistant builds; the author
inspects and decides at checkpoints. Each phase ends with a short findings entry. What
each part needs beyond SREF's scale is recorded in
[production_practice.md](production_practice.md), section by section.

---

## Phase 5 — Error Analysis + One Fix Loop
**Status:** done (2026-10-13) — failures labelled and grouped into four
types; one fix loop (retry on error) measured: no gain
**Goal:** Learn how the agent fails, and change one thing because of it.
- A failure taxonomy built from the labels
- One fix loop: one change aimed at a failure type, re-run, and judged
  against the noise floor. Done as a retry setting aimed at crashes; a
  prompt change for the largest type (coded values) was not tried

**Done when:** the taxonomy is written and one fix is measured.

---

## Phase 6 — Judge
**Status:** done (2026-10-13) — judge agrees with human labels on 10 of 11
**Goal:** Grade an answer that has no exact right answer, and check the grader.
- The agent writes a prose answer from its rows
- A faithfulness judge (`gemma4:12b`) grades prose against rows
- A person labels ~10; the judge's agreement with those labels is measured

**Done when:** the judge's agreement with human labels is reported.

---

## Phase 7 — Experiment + Regression Gate
**Status:** done (2026-10-16) — qwen 5/30 vs `gpt-6.1-sol` 22/30 as fuse
experiments on dataset `sref-gold`; `evals/gate.py` runs. Read case by case,
only 4 of sol's 8 misses were its own (findings article 12)
**Goal:** Compare two setups the way teams do, and guard against slipping back.
- One fuse dataset + experiment: qwen vs one OpenAI model on the same cases
- A regression gate: a check that fails when the score drops below a line

**Done when:** the comparison is readable in fuse and the gate runs.

---

## Eval plan (between Phases 7 and 8)
**Status:** done (2026-10-20) — [eval_plan.md](eval_plan.md), reviewed
**Goal:** The eval plan SREF should have had on day one, written after the
build from what the runs taught and checked against current practice: a
worked example for future projects.
- Decisions the eval supports, golden set, success criteria, graders and
  their validation, metrics defined precisely, noise, review loop,
  comparisons, regression rule, cadence, gaps, day-one lessons
- Written before Phase 8 because it defines the metrics the deliverable
  reports
- Afterwards: a separate doc of proposed edits to the author's AI field manual, outside this repo

**Done when:** the plan is reviewed and accepted.

---

## Phase 8 — Eval Report
**Status:** done (2026-10-20) — `docs/sref_eval_report.pdf`, reviewed
**Goal:** The project's results in one document, readable without the
tracing tools: the capstone.
- `evals/report.py` computes the metrics in [eval_plan.md](eval_plan.md) §7
  and the case-by-case grids from the run record, and fills them into the
  report's narrative template; numbers are generated, never typed
- Content: key findings, method, results (qwen vs sol), where the failures
  came from, deep-dives, checking the graders, experiments and the gate,
  limitations
- Rendered to PDF with pandoc → HTML → Chrome
- Why no Streamlit: the product is the measurement and the story is the
  findings; the interactive views already exist in the tracing tools, shown
  as screenshots

**Done when:** the report PDF builds from the run record and it has
been reviewed.

---

## Phase 9 — Wrap
**Status:** done (2026-10-21) — Technical Reference and README written,
docs reviewed, public copy published
**Goal:** Close the project cleanly and present it.
- Technical Reference PDF, in the same structure as the author's earlier
  projects: overview, architecture, component deep-dives, data reference, operations
- README linking the report and the Technical Reference
- Last step, a public copy: a `build_public_repo.sh`
  copies the tracked files, minus an exclude list, into a fresh repo with
  clean history. This repo stays the private working copy. Tone pass and a
  code license first. The copy leaves out `progress.md`, `CLAUDE.md` and the
  script, and trims personal details from `infra.md`

**Done when:** the public copy is published.

---

## Open Decisions
- None

## Decided
- OpenAI comparison model — `gpt-6.1-sol` (Phase 7). Temperature fixed at 1 by the model
- Judge model — `gemma4:12b` (Phase 6)
- Labelling tool — Langfuse only (Phase 5, findings article 8)
- `eval_plan.md` — yes, written after Phase 7 as a worked example checked against current practice
- Phase 8 form — a report PDF; Streamlit dropped (2026-10-16)
- Public repo — yes, as a fresh copy built by script, not this repo made public, so its history stays private (2026-10-20)
- Code license — MIT, added in the public copy (2026-10-21)
