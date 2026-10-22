# SREF — Eval Plan
**Last updated:** 2026-10-20

> **About this plan.** It was written after Phases 0–7 were built and run, as
> the eval plan SREF should have had on day one, filled in with what the runs
> taught. It is a worked example of an eval plan for an LLM system, checked
> against current practice (October 2026; sources at the end) rather than
> written from theory.
>
> Each section has up to three parts: **Practice** (what current practice
> recommends), **SREF** (what this project does, with the evidence), and
> **Day one → learned** (what a plan written up front would have said, and
> what changed). Where SREF falls short of practice, the section says so.

**Related:** [overview.md](overview.md) (what SREF is) ·
[gold_cases.md](gold_cases.md) (how cases are written) ·
[findings.md](findings.md) (what the runs showed; cited below as
*findings article N*) ·
[production_practice.md](production_practice.md) (what scale adds)

---

## 1. What the eval must decide

**Practice.** An eval exists to support decisions. Name them first; each
needs its own evidence, and a metric that informs none of them is noise.

**SREF.** Four decisions:

| Decision | Evidence it needs | Where |
|---|---|---|
| Is the agent good enough to answer this kind of question? | Pass rate and silent-failure rate on the gold set, read case by case | §7, findings articles 9 and 12 |
| Did a change help? (prompt, schema docs, retry, model) | Paired comparison against the same cases and attempts, judged against the noise | §8, §10 |
| May a change ship? | The regression gate | §11 |
| Can a grade be trusted? | Grader validation: comparator tests, judge agreement with a person, gold audits | §6 |

**Day one → learned.** Day one would have named only the first. The project
spent most of its effort on the last: whether the instrument could be
trusted.

---

## 2. System under test

A text-to-SQL agent: one model call turns a question plus a schema block into
SQL, which runs read-only against a frozen NBA database (SHA-256 pinned);
optionally a second call writes a one- or two-sentence answer from the rows.
Run settings — model, provider, temperature, prompt version, schema mode
(`columns` or `docs`), retries, attempts — are config, recorded in every run.
Details: [overview.md](overview.md) §3–4.

**What is graded:** the rows the agent's SQL returns (correctness), and the
prose answer against those rows (faithfulness). Never the SQL text.

---

## 3. Capability and regression suites

**Practice.** Two kinds of eval with different jobs. A **capability** suite
asks what the agent can't yet do; it should start at a low pass rate and give
a hill to climb. A **regression** suite protects what already works; it
should pass near 100%, and a drop means something broke. Cases graduate from
capability to regression once they pass reliably. Not every eval should
block a release.

**SREF.** One 10-case set does both jobs. Against qwen it is a capability
suite (5/30 passing); against `gpt-6.1-sol` it is approaching a regression
suite (22/30, and 6 cases pass every attempt). The gate (§11) treats it as a
regression suite throughout.

**Day one → learned.** Split them. The six cases sol passes on every attempt
are regression candidates; the rest stay capability cases. A gate on a
capability suite mostly measures noise.

---

## 4. Golden set

**Practice.**
- **Source cases from real failures** and known risks, prioritised by impact;
  20–50 cases is a useful start. Don't write evaluators for failures you
  have only imagined.
- **A reference solution per case,** to prove it is solvable and the grader
  works.
- **Unambiguous:** two domain experts would reach the same pass/fail verdict.
- **Balanced:** cases where a behaviour should happen and where it should not.
- **Audit the gold.** Published text-to-SQL benchmarks have wrong gold in
  roughly half their items. A 0% pass rate across many attempts is more often
  a broken case than a failing agent.
- **Versioned, owned, kept out of prompts.**

**SREF.** 10 cases, written before any agent existed, each aimed at a trap
found by profiling the data ([data_dictionary.md](data_dictionary.md)):

| Tier | Cases | Expected behaviour |
|---|---|---|
| baseline | `best-home-record-1996-97` | Answer: season encoding, one row |
| trap | `lakers-ppg-2022-23` · `all-star-game-count` · `all-star-average-score` · `top-rebounding-teams-1982-83` · `attendance-leader-2014-15` · `fewest-points-in-a-quarter-2018-19` · `biggest-improvements` | Answer correctly despite a data trap: home/away split, duplicate spellings, era NULLs, coverage gaps, text-typed numbers, a missing season |
| hard | `single-game-scoring-high-2015-16` | Derive player points from play-by-play text |
| refusal | `player-salary-refusal` | Decline: no salary data exists |

Each answering case carries **gold SQL** (the reference solution), an
independent **check query** that must agree with it, a **contract** for
comparison, and **provenance** (a published record, or two agreeing
queries). Tests reject any case whose gold fails, returns the wrong row
count, or disagrees with its check. Leak tests keep the gold out of the agent: the
build fails if a case question or a fragment of gold SQL appears in the
agent's prompts or schema block, or if the schema block uses instructional
wording ("trap", "make sure", "instead of"). Writing rules:
[gold_cases.md](gold_cases.md).

**Gaps against practice.**
- **Size.** 10 cases, below the 20–50 starting point; any difference smaller
  than about two cases is noise (§8).
- **Balance.** One refusal case against nine answerable ones.
- **Source.** Cases came from data profiling, not observed failures. That
  is a sound middle path — the traps were real, not imagined — but it
  missed a trap profiling didn't look for (below).
- **Ambiguity.** Two cases were not two-expert clear: one's answer form (a
  fraction or percentage points) and one's required columns (the runner-up's
  name) were left implicit (findings article 12).

**Day one → learned.**
- **The answer's form is part of the case.** State units, scale, rounding
  and which columns are required, and trace every required column to words
  in the question.
- **Profile tables against each other.** `game` and `line_score` disagree on
  home and away in about half of the games they share. Single-table
  profiling missed it; a stronger model found it.
- **Re-audit the gold whenever a stronger model runs.** qwen's failures never
  reached the gold's weak points; sol's did.

---

## 5. Success criteria

**Practice.** Pass or fail per case, not a 1–5 score: a forced decision
doesn't hide uncertainty in the middle of a scale. Finer grades are for
diagnosis. Grade the outcome, not the path taken to it.

**SREF.**
- **Correctness is the result set.** Gold SQL and agent SQL run on the same
  connection against the same snapshot, at eval time; the comparator
  compares rows, never SQL text. Any query that returns the right rows
  passes.
- **One pass line.** A case attempt passes on `exact`, `tolerance` (equal
  once rounded to the contract's decimals) or `refusal_correct`. Everything
  else fails.
- **Verdicts diagnose.** The five failing verdicts say *how* an attempt
  failed: `unordered`, `superset`, `subset`, `wrong`, `error`.
- **Refusals are a 2×2:** asked to decline and declined (`refusal_correct`),
  should have declined but answered (`refusal_absent`), should have answered
  but declined (`refusal_unexpected`).
- **Faithfulness is separate and also binary:** the prose answer says only
  what its rows say. Graded by a judge (§6); never mixed into correctness.

**Day one → learned.** Day one would have written "the answer is correct".
Correct had to be specified per case — units, rounding, required columns,
order — in the contract, or the grader decides it silently.

---

## 6. Graders, and how each is validated

**Practice.** Code graders wherever a rule can decide; an LLM judge only
where judgment is needed, one judge per failure mode; humans to calibrate
both. **Every grader is validated** before its numbers count. A judge is a
classifier: report how often it catches a bad answer and how often it
passes a good one, on 100–200 labelled examples, with the rules written on
one split and measured on another.

**SREF.**

| Grader | Grades | Validated by | Status |
|---|---|---|---|
| Comparator (code) | Correctness | Unit tests per verdict; the same question written two ways must come back `exact`; known-wrong paths must not | ✓ — but findings article 12 found a right answer in another unit scored `wrong` |
| Gold SQL + check query | The reference answer itself | Two independent queries agree; published records where they exist | ✓ — the home/away disagreement shows its limit |
| Faithfulness judge (`gemma4:12b`) | Prose vs rows | A person labelled 11 answers blind, 2 of them planted as unfaithful. Agreement 10/11; the one miss was lenient (passed an unfaithful answer) | Partly: 11 labels, rules and test on the same set |
| Domain expert (one person) | Final call on labels and disputed verdicts | — | The judgment the other graders are calibrated to |

**Gaps against practice.** The judge's validation is too small and not held
out: its rules came from labelling the same 11 answers. A fair estimate
needs a separate set of 100+ labels with both passes and fails. The
comparator was validated against cases written for it, not against right
answers in unexpected forms. No off-the-shelf faithfulness metric (DeepEval
or Ragas) was run beside the custom judge on the same human labels, so
whether a generic metric agrees is untested.

**Day one → learned.** Plan grader validation as its own work item, with its
own labelled set. The most consequential finding of the project (findings
article 12) was about the graders, not the agent.

---

## 7. Metrics

**Practice.** Pick a few, define each precisely — which outcomes count, over
what denominator — and avoid weighted composites. Report consistency across
attempts as well as the average.

**SREF.** All computed from the run record; one row per case attempt.

| Metric | Definition | qwen (`local-docs-r3`) | sol (`openai-docs-r3`) |
|---|---|---|---|
| **Pass rate** (strict) | Attempts with `exact`, `tolerance` or `refusal_correct`, over all attempts | 5/30 (17%) | 22/30 (73%) |
| Pass rate (lenient) | Strict plus `unordered` and `superset` | 5/30 | 22/30 |
| **Silent-failure rate** (headline) | Attempts that returned an answer without error and did not pass — `wrong`, `subset`, `superset`, `unordered`, `refusal_absent` — over all attempts | 17/30 (57%) | 8/30 (27%) |
| Silent share of failures | Silent failures over all failing attempts | 17 of 25 | 8 of 8 |
| Loud-failure rate | `error` (SQL that did not run) or `refusal_unexpected`, over all attempts | 8/30 | 0/30 |
| **pass^k** (consistency) | Cases that pass on every one of k attempts | 1/10 | 6/10 |
| pass@k | Cases that pass on at least one of k attempts | 2/10 | 9/10 |
| Verdict stability | Cases whose verdict changed across attempts; those that crossed the pass line | 5 changed, 1 crossed | 3 changed, 3 crossed |
| **Judge agreement** | Agreement with human labels, split into lenient misses (passed an unfaithful answer) and strict misses | 10/11; 1 lenient, 0 strict (Phase 6 run) | — |
| Cost | Provider-priced tokens, cached input priced separately | $0 (local) | ~$0.78 |
| Latency | Model seconds per attempt, median | 5.7 s | 3.7 s |

**Why silent failure is the headline.** A loud failure announces itself; a
silent one looks like a right answer. The stronger model cut silent failures
by half but every one of its failures was silent — a rate that falls while its
share rises. An empty result counts as silent: no error, though easier to
spot than a wrong number. 4 of qwen's 17 silent failures were empty; none of
sol's.

**Day one → learned.** "Silent-failure rate" was the headline from day one
but was never defined until this plan. Define every reported metric in
writing, with its denominator, before the first comparison.

---

## 8. Trials and noise

**Practice.** Model outputs vary, so run several trials per case and treat a
score as an estimate with an interval. On small sets the interval is wide:
30 items at 50% carry roughly ±17 points.

**SREF.**
- **Three attempts per case** in every comparison run (`-r3` configs). At
  temperature 0 an identical prompt repeats itself, so attempts 2–3 shuffle
  the order of tables in the schema block: same facts, different prompt.
- **Measured noise floor.** About one case in ten crosses the pass line
  between attempts at temperature 0 (findings article 1). sol, fixed at
  temperature 1 by the model, crossed it on 3 of 10 cases.
- **Intervals.** 95% Wilson intervals on the eval runs: qwen 5/30 →
  7–34%; sol 22/30 → 56–86%. The two arms are clearly apart; a two-case
  difference between similar setups would not be. The 30 attempts are not
  independent (three per case), so the true intervals are wider still.

**Day one → learned.** Measure the noise floor before the first comparison,
and size the set from it. SREF learned this in week two, when an unrelated
prompt edit flipped a verdict.

---

## 9. Error analysis and review

**Practice.** Read outputs before writing rubrics. Note each failure in free
text, then group the notes into a failure taxonomy and count by type. Keep
going until new outputs stop revealing new failure types. One domain expert
makes final calls. Re-run error analysis after every major change. Record
failures that are not the model's fault, too.

**SREF.**
- **Taxonomy.** 25 failing attempts grouped into four types: coded value
  wrong (12), missing column (7), question misread (3), wrong shape (3)
  (findings article 9).
- **Whose miss.** Every failure is assigned to the agent, the gold, the
  grader or the question. Against qwen all 25 were the agent's. Against sol,
  4 of 8 were not (findings article 12).
- **Labelling split.** A model drafts each label with the database facts
  checked; a person reviews a sample. Langfuse keeps the draft and the review apart,
  which is why it is the only labelling tool (findings article 8).
- **Traces as the reading list.** Every attempt is traced to Phoenix,
  Langfuse and LangSmith. The comparator's verdict goes to each tool as a
  native grade (`pass`, `verdict`), so failing traces can be filtered and
  read step by step; labels made in Langfuse come back to the run record by
  trace id. A trace shows what the code did; only the grade shows whether
  it was right ([observability_tools.md](observability_tools.md)).
- **Read the transcripts.** Findings articles 9–12 all came from reading rows
  and SQL side by side, not from a score.

**Day one → learned.** Error analysis has to start again whenever the model
changes: a stronger model moves the failures from the agent to the eval.

---

## 10. Comparisons and experiments

**Practice.** One versioned dataset; every candidate runs against the same
version, with one change at a time. Compare per item — what improved, what
regressed — not just per score. Report cost and latency beside quality.

**SREF.**
- **Dataset.** The gold cases are a Langfuse dataset (`sref-gold`), questions
  only: gold runs at eval time and travels with each experiment trace as
  the expected output.
- **Experiments.** `--experiment` records each attempt as one experiment.
  qwen against sol, attempt 1: ↗ 6 ↘ 0 in Langfuse's compare view.
- **One change at a time — except where the model forces more.** sol accepts
  only temperature 1 and needs a larger output cap for hidden reasoning;
  both are recorded as part of what was compared.
- **Measured fixes.** Documentation versus raw columns (findings articles 1–2) and a
  retry on SQL errors (findings article 10, no gain) were each a run setting compared
  attempt by attempt.

---

## 11. Regression rule

**Practice.** A gate is a paired experiment, not a raw score threshold:
compare the same cases against a pinned baseline, report the paired
difference with an interval, and fail only when the interval excludes no
change. Use hard gates for critical cases (any single loss fails) and soft,
interval-based gates for the rest. Run it in CI on every change. An
unvalidated judge does not gate.

**SREF.** `evals/gate.py`, run by hand against a baseline pinned per config:

1. **Score line (soft).** The pass rate must not fall more than 10 points
   below the baseline's — a fixed margin from the measured noise, about 3 of
   30 attempts.
2. **No lost stable case (hard).** A case that passed every baseline attempt
   must not fail every attempt now. 3/3 → 2/3 can be noise; 3/3 → 0/3 is not.

The gate refuses to compare different snapshots, case sets or attempt
counts, and reports which settings changed. The judge does not gate.

**Gaps against practice.** A fixed margin, not an interval; by hand, not in
CI; baselines in a gitignored folder on one machine.

**Day one → learned.** Gate the regression suite only (§3). The margin
should come from a measured interval, and narrow as the set grows.

---

## 12. Cadence, ownership and maintenance

**Practice.** Run the regression suite on every change and the full set on
a schedule; re-run error analysis after major changes and on fresh traffic
every few weeks. The suite is a living artifact with an owner. Retire or
rewrite saturated evals: a suite at 100% catches regressions but gives no
signal for improvement.

**SREF.** A learning project on a frozen snapshot, so most of this is
recorded, not exercised:
- **When:** dev runs (`SREF_ENV=dev`, one attempt) on every change while
  building; eval runs (`SREF_ENV=eval`, three attempts) for any comparison
  that counts; the gate after any eval run.
- **Owner:** one person owns the gold set and the final call on labels; an
  AI coding assistant builds, drafts labels, and checks database facts.
- **Saturation:** not reached. Against sol, six cases are near it.

---

## 13. Not covered

- **Online evaluation** on live traffic, user feedback, drift monitoring.
- **Safety and adversarial cases:** prompt injection through the question,
  attempts to write or exfiltrate data. The DB layer blocks writes, but no
  case tests it.
- **Ambiguous questions** the agent should ask about rather than answer.
- **Multi-turn conversations** and multi-step agents.
- **Cost and latency budgets.** Both are recorded; neither is gated.

---

## 14. What we would change on day one

1. **Define every metric in writing,** with its denominator, before the first
   run (§7).
2. **Measure the noise floor first,** and size the set from it (§8).
3. **Specify the answer's form in every case:** units, scale, rounding,
   required columns (§4).
4. **Profile tables against each other,** not only one at a time (§4).
5. **Split capability from regression,** and gate only the regression
   suite (§3, §11).
6. **Plan grader validation as its own work,** with a held-out labelled set
   (§6).
7. **Start error analysis again whenever the model changes,** and ask whose
   miss each failure is (§9).
8. **Balance the set:** more cases where the right behaviour is to decline or
   to ask (§4, §13).

---

## Sources

Current practice as checked on 2026-10-16:

- [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) — Anthropic: tasks and trials, grader types, capability vs regression, pass@k and pass^k, saturation
- [AI Evals FAQ](https://hamel.dev/blog/posts/evals-faq/) — Hamel Husain and Shreya Shankar: error analysis first, binary labels, judge validation
- [Text-to-SQL Benchmarks are Broken](https://www.vldb.org/cidrdb/papers/2026/p5-jin.pdf) — CIDR 2026: annotation errors in BIRD and Spider 2.0
- [SpotIt: Evaluating Text-to-SQL Evaluation with Formal Verification](https://arxiv.org/html/2510.26840v2)
- [How to Turn LLM Evaluation into a Reliable CI Regression Gate](https://oneuptime.com/blog/post/2026-08-31-llm-evaluation-ci-regression-gate/view) — paired comparison and intervals
- [LLM Eval Sample Size Confidence Intervals](https://qaskills.sh/blog/llm-eval-sample-size-confidence-intervals)
- [LLM Readiness Harness](https://arxiv.org/pdf/2603.27355) — hard and soft gates
