# Soapy Referee (SREF) — Overview
**Last updated:** 2026-10-21

---

## 1. What SREF Is

**Soapy Referee (SREF)** is an evaluation and observability harness for an
LLM agent that answers natural language questions about NBA history by
writing SQL against a frozen database snapshot.

**The harness is the product. The agent is the fixture** — a deliberately
small text-to-SQL agent, written in pure Python, that exists to be
measured. The deliverable is the measurement system around it: an
executable gold set, a result-set comparator, a validated LLM judge,
regression gates, and trace-level observability in **Arize Phoenix**,
**Langfuse**, and **LangSmith**.

Model performance is secondary. One open model runs locally for everyday
development, and one closed OpenAI model provides a single point of comparison.

SREF is a *learning-first* project focused on building durable, transferable
skills in LLM evaluation engineering. NBA data is the domain; the methods
apply to any system that turns questions into queries.

---

## 2. Core Concept

> "The agent plays. The referee makes the call."

Every agent answer is judged against an oracle that is itself a program:
gold SQL, executed at eval time against the same frozen snapshot the agent
queried. Both result sets are canonicalized and compared into a **verdict
class**, the prose answer is checked for faithfulness to the rows actually
returned, and every step is traced.

The headline metric is not accuracy. It is the **silent-failure rate** —
answers that are wrong, raise no error, and look plausible.

### Metrics

Counted over case attempts in the run record (each case runs three times in
a comparison). Full definitions and worked numbers: [eval_plan.md](eval_plan.md) §7.

- **Pass rate** — attempts graded `exact`, `tolerance` or `refusal_correct`,
  over all attempts. The one pass line every other number hangs off.
- **Silent-failure rate** — attempts that returned an answer without error
  and did not pass (`wrong`, `subset`, `superset`, `unordered`, or answering
  a question it should have declined), over all attempts. Also worth
  reading as a share of failures: a stronger model can cut the rate while
  every failure it still makes is silent.
- **Loud-failure (error) rate** — attempts whose SQL did not run, or that declined a
  question the data can answer. Loud failures: they announce themselves.
- **Stability** — cases whose verdict changed across attempts, and how many
  of those crossed the pass line. Plus pass^k: cases that pass every
  attempt. A difference between two setups smaller than the instability is
  noise.
- **Judge agreement, by direction** — how often the faithfulness judge
  agrees with a person, split into lenient misses (it passed an unfaithful
  answer, the costly kind) and strict misses.

### Example Questions

| Question | What it tests |
|---|---|
| "Which team had the best home record in 1996-97?" | Baseline — season encoding, single-answer cardinality |
| "What did the Lakers average in points per game in 2022-23?" | Home/away split across columns — filtering one side returns a believable wrong number |
| "How many All-Star games are in the database?" | Inconsistent categorical spelling that duplicates games |
| "Which two teams averaged the most rebounds per game in 1982-83?" | Era-dependent NULLs — some games of that era have no rebounds recorded; counting them as zero gives believable wrong averages |
| "Who scored the most points in a single game in 2015-16?" | Hard tier — player totals must be derived from play-by-play text |
| "How much was LeBron James paid in 2019-20?" | Correct behavior is **refusal** — no salary data exists anywhere in the snapshot |

---

## 3. Tool Stack

| **Layer** | **Tool / Service** | **Purpose** | **Status** |
|---|---|---|---|
| Data Snapshot | **SQLite** (`nba.sqlite`) | Frozen, checksummed NBA database — the ground truth for every case | In use |
| Query Engine | **DuckDB** + `sqlite` extension | Read-only `ATTACH` of the snapshot; one execution path for gold and agent SQL | In use |
| Environment | **uv**, Python 3.12 | Dependency management and reproducible environment | In use |
| Gold Set | **YAML** cases | Question, gold SQL, comparison contract, expected behavior | In use |
| Comparator | **Python** + **pytest** | Canonicalize result sets, classify verdicts; unit-tested | In use |
| Run Record | **JSON** + **JSONL** in `runs/` | Run header (config, snapshot SHA, commit) plus one flat row per case — the source of truth that traces are a view over | In use |
| Agent | **Pure Python** (no framework) | Schema context → prompt → SQL → execute → prose answer | In use |
| Agent Schema Docs | **YAML** | Agent-facing schema description — facts only, no query hints | In use |
| Local Model Runtime | **Ollama** | Serves local models; called through its native API, which can refuse an oversized prompt instead of silently truncating it | In use |
| Development Model | **qwen2.5-coder:14b** (Ollama) | Primary agent model for everyday runs — fast, free, code-tuned | In use |
| Comparison Model | **OpenAI** `gpt-6.1-sol` | One closed model, run against the same suite as a fuse experiment (Phase 7) | In use |
| LLM Judge | **gemma4:12b** (Ollama) | Prose-vs-rows faithfulness only, validated against human labels | In use (agrees with human labels on 10 of 11) |
| Instrumentation | **OpenTelemetry** + **OpenInference** | Backend-agnostic spans wrapped around the eval runner | In use |
| Observability — primary | **Arize Phoenix** (local) | Traces and grades as annotations | In use |
| Observability — primary | **Langfuse** (self-hosted, Docker) | Traces, grades as scores, the only human-labelling tool, the gold-set dataset and the model comparison experiments | In use |
| Observability — comparison | **LangSmith** (cloud, free tier) | Third backend on the same traces, for a side-by-side comparison | In use |
| Eval Report | **pandoc** + Chrome → PDF | The results, read case by case without the tracing tools; numbers generated from the run record (`evals/report.py`) | In use |
| Version Control | **GitHub** / Markdown | Code, gold set, and project docs | In use |

---

## 4. Eval Loop Architecture

```
            evals/cases/*.yaml
     (question · gold SQL · contract · expected behavior)
                      │
                      ▼
                   Runner ─────────── OTel / OpenInference spans ──► Arize Phoenix
                      │                                          ├──► Langfuse (+ experiments
                      │                                          │    on dataset sref-gold)
                      │                                          └──► LangSmith
         ┌────────────┴────────────┐
         │ question                │ gold SQL
         │ + schema context        │
         ▼                         │
   SQL agent (LLM)                 │
         │ agent SQL               │
         ▼                         ▼
   DB layer — DuckDB, read-only ATTACH, statement guard, timeout, row cap
         │                         │
         ▼                         ▼
    result set A              result set B           ◄── frozen nba.sqlite
         └────────────┬────────────┘
                      ▼
      Comparator — project → resolve IDs → round → sort → classify
                      │
                      ▼
                verdict class
      exact · tolerance · unordered · superset · subset · wrong · error
                      │
                      ▼
          Run record — runs/<run_id>/  (the source of truth)
                      │
      ┌───────────────┼──────────────┬──────────────┐
      ▼               ▼              ▼              ▼
   Grades to      Judge — prose   Regression     Eval report
   the tools        ↔ rows          gate         (generated)
```

Gold SQL and agent SQL always run through the **same** DB layer, in the same
run, against the same snapshot. Nothing is compared against stored results.
The schema context is either the raw column list or the agent schema docs,
set per run.

---

## 5. Data Sources

### NBA Database Snapshot — Primary and Only Source
- **What it is**: `nba.sqlite` from the Kaggle dataset `wyattowalsh/basketball`, sourced from stats.nba.com
- **License**: CC BY-SA 4.0
- **Coverage**: games from 1946-11-01 through 2023-06-12; play-by-play from 1996-97 onward
- **Size**: 2.2 GB, 16 tables, no indexes
- **Integrity**: frozen and read-only; SHA-256 `65b6412234d2d8817678d04f7cd2548aec82fec02b6c5b86679cade7572c21c7`
- **Core tables**: `game` (65,698 rows, one row per game with home and away side by side), `play_by_play` (13.6M events), `line_score`, `other_stats`, `game_info`, `game_summary`, `player`, `common_player_info`, `team`, `team_history`, `draft_history`
- **Not present**: player-level box scores (derivable from play-by-play text only), salaries or contracts, and anything after June 2023. Advanced metrics such as usage rate are absent but derivable, with effort, from play-by-play

The schema's inconsistencies — duplicated game IDs, mixed column types,
era-dependent NULLs, dimension tables that don't cover the fact tables — are
**kept intentionally**. They are what the harness measures.

Full table-by-table profile and verified trap catalog: [data_dictionary.md](data_dictionary.md)

### Agent Schema Docs — What the Agent Sees
The agent never sees the data dictionary: its trap catalog is effectively the
answer key. The agent gets its own schema documentation, written the way a
competent data team would document a warehouse:

- **Included (facts):** table purpose and grain, column meanings, value
  encodings (e.g. `season_id` = season-type digit + start year, `21996`),
  observed categorical values, coverage limits, and join keys
- **Excluded (hints):** the trap catalog, advice on how to write queries, and
  example queries for specific questions

Whether a run gives the agent these docs or only the raw column list is a run
setting, so the effect of documentation on failures is itself measurable.

---

## 6. Design Principles

1. **The agent always sees the raw tables.** The data is never cleaned,
   remodeled, or wrapped in friendlier views for the agent. Documentation may
   describe the tables; nothing changes them. The mess is the measuring
   instrument.
2. **Nothing from the eval design leaks into the agent.** Gold SQL, the trap
   catalog, and query hints never reach its prompt. Agent schema docs state
   facts, never how to query.
3. **Gold is a program, not a stored answer.** Gold SQL executes at eval
   time. Expected rows are never stored as the answer key — previews in the
   run record and the traces are for reading — and SQL is never compared as
   text. Any query that produces the right rows is correct.
4. **Verdicts diagnose; one line decides.** Seven verdict classes say how
   an attempt failed; one pass line decides whether it passed. A lenient
   line (adding `unordered` and `superset`) is reported beside it.
5. **Grain lives in the contract.** Each case declares order mode, expected
   cardinality, rounding, and which columns hold teams, players, and seasons.
   Column naming is presentation; entity comparison is on IDs.
6. **The judge never grades correctness.** It grades only whether the prose
   answer is faithful to the rows returned, and its own accuracy is measured
   and reported.
7. **Build the instrument before the thing it measures.** The comparator is
   built and unit-tested before the agent exists.
8. **Instrument the loop, not the agent.** Tracing wraps the runner, so
   agents can be swapped without re-instrumenting.
9. **Every variable is config.** Model, temperature, prompt version,
   schema-context mode, and run index are injected, never edited in source.
10. **Gold provenance is reported.** Cases are calibrated against published
   records or cross-checked by writing the gold SQL two different ways.
   Irresolvably ambiguous cases are cut, never guessed.

---

## 7. Scope & Non-goals

**In scope**
- Executable gold set with documented provenance
- Result-set comparator with verdict classes and unit tests
- Refusal cases for questions the data cannot answer
- Validated prose-faithfulness judge
- Human error analysis and a failure taxonomy
- Tracing to Arize Phoenix, Langfuse, and LangSmith from one instrumentation layer
- Run-to-run variance, cost, and latency — one local model versus one closed model
- Measuring how agent schema docs change failure patterns
- Model comparison as a Langfuse experiment, and a regression gate
- An eval report generated from the run record, plus a Technical Reference

**Non-goals**
- **Not a production NBA stats tool.** Agent accuracy is measured, not maximized.
- **Not a model benchmark.** One development model and one closed comparison
  model; which scores higher is secondary to how the scoring works.
- **Not data engineering.** No cleaning, remodeling, or refreshing the snapshot.
- **Not current.** The snapshot is frozen; recency is irrelevant to the evaluation.
- **Not a test of the data's accuracy.** The snapshot is ground truth by
  construction — the question is whether the agent faithfully translates
  questions into queries over it.
- **No BI dashboards.** Results are a generated report, not a dashboard.
- **No fine-tuning.**

---

## 8. Folder Structure

```
sref/
├── agent/                       # System under test
│   ├── build_schema_docs.py     # Generates the schema catalog from the snapshot
│   ├── schema_docs.yaml         # Agent-facing schema docs — facts only
│   ├── schema_context.py        # Renders the schema block per run setting
│   ├── prompts/                 # Versioned prompts: SQL, retry, prose answer
│   ├── prompt.py                # Prompt contract — build the call, parse the reply
│   ├── llm.py                   # Routes each call to the config's provider
│   ├── completion.py            # What every provider returns and raises
│   ├── ollama.py                # Ollama client
│   └── openai_api.py            # OpenAI client (comparison arm)
│
├── evals/                       # The harness
│   ├── cases/                   # Gold set — YAML, the core asset
│   ├── cases.py                 # Case loader and validation
│   ├── configs/                 # Run configs — one file per arm; judge/ and gate/ settings
│   ├── config.py                # Run config loader
│   ├── db.py                    # DuckDB connection, ATTACH, guard
│   ├── snapshot.py              # Snapshot fingerprint check
│   ├── resolve.py               # Team, player, and season ID resolution
│   ├── comparator.py            # Canonicalize + classify verdicts
│   ├── runner.py                # Orchestration
│   ├── record.py                # Run record writer
│   ├── tracing.py               # OTel spans to Phoenix, Langfuse, LangSmith
│   ├── scores.py                # Verdicts to the tools as grades
│   ├── judge.py                 # Prose-vs-rows faithfulness judge
│   ├── judge_prompts/           # Versioned judge prompts
│   ├── dataset.py               # Gold cases as the Langfuse dataset
│   ├── gate.py                  # Regression gate against a pinned baseline
│   ├── metrics.py               # The eval's metrics, one definition each
│   ├── report.py                # Eval report: narrative template + generated tables
│   └── api.py                   # HTTP helper for the tools' REST APIs
│
├── tools/
│   └── build_pdf.sh             # Markdown → PDF (pandoc → HTML → Chrome)
│
├── tests/                       # Unit, gold-case, and leak tests
│
├── infra/                       # Docker Compose: Phoenix + self-hosted Langfuse
│
├── runs/                        # Run records — the four the report uses
│
├── data/                        # Gitignored
│   └── nba.sqlite               # Frozen snapshot — ground truth
│
├── docs/                        # Project documentation
│   ├── overview.md              # This document
│   ├── prd.md                   # Build phases and done-when criteria
│   ├── eval_plan.md             # Metrics, graders, gate — the eval's definitions
│   ├── sref_eval_report.md/.pdf # Soapy Referee Report (generated; Phase 8)
│   ├── sref_technical_reference.md/.pdf # System reference (Phase 9)
│   ├── report/                  # Report template and print stylesheet
│   ├── images/                  # Screenshots used in the report and observability_tools.md
│   ├── data_dictionary.md       # Table profiles and trap catalog
│   ├── gold_cases.md            # Case-writing rules and coverage
│   ├── findings.md              # What the harness has shown
│   ├── production_practice.md   # What each eval part needs at production scale
│   ├── observability_tools.md   # OTel/OpenInference and the three tools
│   ├── code_inventory.md        # What each file is, and why
│   └── infra.md                 # Services, ports, URLs, credentials
│
├── README.md                    # Project front page
├── .env.example                 # Provider and observability keys
├── pyproject.toml               # uv project definition
└── uv.lock
```

---

## 9. Learning Objectives

- Build a gold set whose oracle is executable, auditable, and reported with its provenance
- Design a result-set comparator — canonicalization, verdict classes, strictness tiers
- Measure silent failures rather than headline accuracy alone
- Build an LLM-as-judge and validate it against human labels (agreement, and which way it errs)
- Instrument an LLM system with OpenTelemetry and OpenInference
- Develop hands-on fluency with **Arize Phoenix**, **Langfuse**, and **LangSmith**, and compare them on identical traces
- Run human error analysis through annotation queues and turn it into a failure taxonomy
- Quantify run-to-run variance, cost, and latency, and compare a local open model against a closed model
- Measure how schema documentation changes agent failures — and which failures it cannot fix
- Gate changes on an eval regression suite
