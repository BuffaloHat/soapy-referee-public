# Soapy Referee (SREF)

An evaluation and observability harness for an LLM agent that answers questions about NBA history by writing SQL against a frozen database. It builds every part of an eval around one deliberately small agent — an executable gold set, a result-set comparator, error analysis, a validated LLM judge, a model comparison run as experiments, and a regression gate — with every run traced to Arize Phoenix, Langfuse and LangSmith.

> *The agent plays. The referee makes the call.* SREF isn't a text-to-SQL product: the harness is the product and the agent is the fixture. Every answer is graded by running gold SQL against the same snapshot and comparing result sets, and the headline metric is the **silent-failure rate** — answers that are wrong, raise no error, and look plausible.

→ **[Soapy Referee Report (PDF)](docs/sref_eval_report.pdf)**
*The capstone — what the harness found: a local model against an OpenAI model, case by case, and what the graders got wrong.*

→ **[Technical Reference (PDF)](docs/sref_technical_reference.pdf)**
*Full system reference — architecture, component deep-dives, data reference, operations.*

---

## Findings at a Glance

Ten questions, each asked three times, put to two models: a local 14B model (qwen) and an OpenAI model (sol).

- **sol passed 22 of 30 attempts; qwen passed 5.** But only half of sol's misses were wrong answers. The rest exposed problems in the gold set itself.
- **Most failures were silent:** wrong, no error, and plausible-looking. That was 17 of qwen's 25 failures and all 8 of sol's.
- **Small changes move results.** Reordering the tables in the prompt flipped about one case in ten, so a difference of a case or two is noise.

The full story, case by case, is in the **[Soapy Referee Report (PDF)](docs/sref_eval_report.pdf)**. Every finding, with its evidence, is in [docs/findings.md](docs/findings.md).

---

## The build, phase by phase

| Phase | Eval part | What it teaches |
|---|---|---|
| **1. Instrument first** | DB layer + result-set comparator, unit-tested before any agent existed | Grade outcomes, not SQL text; seven verdicts, one pass line |
| **2. Gold set** | 10 cases aimed at data traps, each with gold SQL and an independent check query | Gold is a program, executed at eval time — and gold can be wrong |
| **3. Agent + runner** | A pure-Python text-to-SQL agent; every setting is config; the run record | Measure the noise floor before comparing anything |
| **4. Observability** | OpenTelemetry + OpenInference to Phoenix, Langfuse and LangSmith | Tracing shows what the code did, not whether it was right |
| **5. Error analysis** | Labels, a failure taxonomy, one measured fix | Fix the failure type, not its symptom; keep negative results |
| **6. LLM judge** | A faithfulness judge, checked against human labels | Know which way the judge errs, not just how often it agrees |
| **7. Experiment + gate** | Local vs OpenAI model as Langfuse experiments; a regression gate | Compare per case; gate on noise-aware rules, not a raw score |
| **8. Eval report** | Metrics and grids generated from the run record | Define every metric with its denominator; never retype a number |

---

## Tech

| Layer | Choice | Why |
|---|---|---|
| Python env | uv (Python 3.12) | Fast, lockfile-clean |
| Data | Kaggle `wyattowalsh/basketball` SQLite, SHA-256 pinned | Real, messy data — its traps are the measuring instrument |
| Query engine | DuckDB, read-only `ATTACH` | One guarded path for gold and agent SQL |
| Agent | **No framework.** Pure Python, thin named steps | Frameworks would hide exactly what the eval measures |
| Models | `qwen2.5-coder:14b` (agent) and `gemma4:12b` (judge) via Ollama; `gpt-6.1-sol` via OpenAI (comparison) | Free local development; one closed model as a point of comparison |
| Instrumentation | OpenTelemetry + OpenInference | One span layer, three backends |
| Observability | Arize Phoenix and Langfuse (local Docker), LangSmith (cloud) | Compare three tools on identical traces; Langfuse for labelling and experiments |
| Reports | pandoc + Chrome headless | Markdown → PDF, numbers generated from the run record |

---

## Pipeline

```
evals/cases/*.yaml  (question · gold SQL · contract)
        │
        ▼
Runner ──────────── OTel spans ──► Phoenix · Langfuse (+ experiments) · LangSmith
        │
        ├── question + schema ──► agent (LLM) ──► agent SQL ─┐
        └── gold SQL ────────────────────────────────────────┤
                                                              ▼
              DB layer — DuckDB, read-only, guard, timeout, row cap ◄── nba.sqlite
                                                              │
                                                              ▼
              Comparator — resolve IDs → round → sort → verdict
                                                              │
                                                              ▼
              Run record — runs/<run_id>/  (the source of truth)
                                                              │
              ┌───────────────┬───────────────┬───────────────┤
              ▼               ▼               ▼               ▼
        grades to tools   judge (prose)   regression gate   eval report
```

---

## Quick start

```bash
uv sync
cp .env.example .env                       # tool keys; OPENAI_API_KEY for the comparison arm
cp infra/.env.example infra/.env           # Langfuse's own secrets — fill in first
cd infra && docker compose up -d && cd ..  # Phoenix :6006 · Langfuse :3100
ollama pull qwen2.5-coder:14b && ollama pull gemma4:12b
```

Download `nba.sqlite` from [Kaggle `wyattowalsh/basketball`](https://www.kaggle.com/datasets/wyattowalsh/basketball) into `data/` and check it: `shasum -a 256 data/nba.sqlite` should print `65b6412234d2d8817678d04f7cd2548aec82fec02b6c5b86679cade7572c21c7`.

```bash
uv run pytest                                                   # ~400 tests, every gold case included
uv run python -m evals.runner --config local-docs               # one pass of the gold set
SREF_ENV=eval uv run python -m evals.runner --config openai-docs-r3 --experiment
uv run python -m evals.scores --run <run_id>                    # grades to the tools
uv run python -m evals.gate --run <run_id>                      # regression gate
```

All commands, workflows and common issues: the [Technical Reference](docs/sref_technical_reference.pdf), Section 5.

---

## Layout

```
sref/
├── agent/               # System under test: schema docs, prompts, model clients
├── evals/               # The harness: cases, comparator, runner, tracing, judge, gate, report
│   ├── cases/           # The gold set — one YAML per case
│   └── configs/         # Run configs (one per arm), judge, gate and report settings
├── tests/               # Unit, gold-case and leak tests
├── infra/               # Docker Compose: Phoenix + self-hosted Langfuse
├── tools/               # build_pdf.sh
├── docs/                # Overview, PRD, eval plan, findings, report, reference
├── data/                # nba.sqlite — gitignored, read-only
└── runs/                # Run records — the four the report uses
```

---

## Docs

| File | Purpose |
|---|---|
| [**Soapy Referee Report** (PDF)](docs/sref_eval_report.pdf) | The capstone: what the harness found |
| [**Technical Reference** (PDF)](docs/sref_technical_reference.pdf) | Architecture, component deep-dives, data reference, operations |
| [**Eval plan**](docs/eval_plan.md) | Metrics, graders and their validation, the regression rule — a worked example |
| [Findings](docs/findings.md) | Twelve findings, each with its evidence |
| [Production practice](docs/production_practice.md) | What each eval part needs at production scale |
| [Overview](docs/overview.md) | Concept, architecture, design principles, scope |
| [PRD](docs/prd.md) | The build path in phases, with done-when criteria |
| [Gold cases](docs/gold_cases.md) | Rules for writing cases, and what the set covers |
| [Data dictionary](docs/data_dictionary.md) | Table profiles and the trap catalog |
| [Observability tools](docs/observability_tools.md) | The three tracing tools compared on identical traces: what each shows, and what each takes to run |
| [Code inventory](docs/code_inventory.md) | What each file is, and why |
| [Infra](docs/infra.md) | Services, ports, credentials |

---

## License

Code is released under the [MIT License](LICENSE).

NBA data from the Kaggle dataset `wyattowalsh/basketball`, sourced from stats.nba.com, licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
