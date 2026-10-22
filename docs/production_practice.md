# SREF — Production Practice

What each part of SREF's eval does, and what the same part needs in a
production eval. SREF exercises each part once, at small scale, on a frozen
dataset; this doc records what full scale adds, so it can serve as a
checklist when building evals for a real AI product.

Each section: **SREF** — what this project does; **In production** — what a
production eval adds. Sections are added as phases complete.

**Related:** [prd.md](prd.md) (the build path) ·
[findings.md](findings.md) (what the runs showed) ·
[observability_tools.md](observability_tools.md) (the tracing tools)

---

## 1. Test cases (the gold set)

**SREF.** 10 hand-written cases across four tiers (baseline, trap, hard,
refusal), aimed at known traps in the data. Each answering case carries gold
SQL and an independent check query that must agree with it, and is checked
against a published record where one exists. Tests prove every case runs,
returns the expected row count, and has no tie at a top-N cutoff. Writing
rules live in [gold_cases.md](gold_cases.md).

**In production:**
- **Size for the decision.** Hundreds of cases, enough that a real
  difference between two setups is larger than run-to-run noise (§4).
- **Coverage by slice.** Cases spread across failure types, user intents
  and difficulty, with counts per slice, so a score can be read slice by
  slice rather than as one number.
- **Fed by real failures.** New cases come from production traces and error
  analysis, not only from what the author thought of up front.
- **Versioned and owned.** The case set is versioned like code, changes are
  reviewed, and every score names the case-set version it ran against.
- **Kept fresh.** When the data, product or users change, cases are
  re-checked; stale gold is worse than no gold.
- **Kept out of training and prompts.** Cases never leak into prompts, few-shot
  examples or fine-tuning data (see §8).
- **The answer's form is part of the case.** Units, scale, rounding and
  which columns are required are written into the question or the
  contract, and every required column traces back to words in the
  question. A stronger model finds every gap here ([findings.md](findings.md)
  article 12).
- **Profile the data across tables, not only within them.** Two tables
  describing the same event can disagree; a gold set built on one-table
  profiling has traps its authors don't know about.

---

## 2. Scoring against a known answer

**SREF.** Gold SQL and agent SQL both run against the same read-only
snapshot, and the comparator compares the result sets, never the SQL text.
Seven verdicts (`exact`, `tolerance`, `unordered`, `superset`, `subset`,
`wrong`, `error`), a per-case contract (ordering, decimals, extra columns),
ID resolution so a team name and a team id can match, and a separate 2×2 for
refusals.

**In production:**
- **Score the outcome, not the wording.** Execute or check the result
  wherever possible; text similarity rewards answers that look right.
- **Verdicts with reasons.** A graded scale (exact, partial, wrong, error)
  plus a machine-readable reason, so failures can be counted by cause.
- **Test the grader.** Unit tests for every verdict, plus known-wrong
  answers that must *not* pass, so a lenient grader cannot inflate scores.
- **One path for gold and agent.** The same connection, timeouts and
  limits for both, so a difference in verdict is a difference in answer.
- **Re-read the misses when the model changes.** A grader tuned on a weak
  model's failures has never been tested by right answers in an
  unexpected form. Review a stronger model's misses case by case before
  quoting its score.

---

## 3. The run record

**SREF.** Every run writes a header (the full config, the data snapshot's
SHA-256, the git commit with a `-dirty` flag, the environment) and one flat
row per case attempt: prompt, raw reply, SQL, verdict, token counts,
timings, the first rows of both answers, and the trace ids. Written as it
runs, so a crash keeps what was done.

**In production:**
- **Stored centrally, queryable across runs.** A table or warehouse, not
  local files, so any two runs can be compared months later.
- **Everything that can change an answer is recorded.** Model and version,
  prompt version, retrieval or context settings, data version, code version.
- **Commit, then run.** A run made from uncommitted code is labelled
  `-dirty`; its commit alone cannot reproduce it. Runs that inform decisions
  start from a clean commit.
- **Changes as settings, not copies.** A new behaviour ships behind a run
  setting with the old behaviour as its default, so before and after run the
  same code and either can be re-run at any time.
- **Retention decided on purpose.** Runs are evidence; keep them as long as
  decisions rest on them, independent of any vendor's retention.
- **Personal data handled.** Prompts and outputs may hold user data; the
  record needs the same access controls and retention rules as the product.

---

## 4. Noise and repeats

**SREF.** At temperature 0 the same prompt gives the same answer, but a
meaningless change (the order of tables in the schema) can flip it. Runs
repeat each case three times with a shuffled table order. About one case in
ten flips between pass and fail, so on 10 cases a one-case difference
between setups is noise.

**In production:**
- **Measure the noise floor before comparing anything.** Repeat with small,
  meaningless perturbations, or at the production temperature.
- **Report scores with a margin of error.** Confidence intervals or a
  significance test on the difference, not two bare percentages.
- **Size the test set from the noise.** The smaller the difference worth
  detecting, the more cases and repeats it takes.
- **Track per-case stability.** Cases that flip are either ambiguous
  (fix the case) or fragile (a finding about the model).

---

## 5. Tracing

**SREF.** OpenTelemetry spans with OpenInference attributes, one trace per
case attempt and one session per run, exported at once to Phoenix, Langfuse
and LangSmith. Every span carries the join keys back to the run record. The
wiring was tuned until all three tools showed what matters
([observability_tools.md](observability_tools.md) §6).

**In production:**
- **Trace production traffic, not only eval runs,** with sampling where
  volume demands it.
- **One instrumentation layer,** vendor-neutral, so the backend can change
  without re-instrumenting.
- **Join keys everywhere.** Trace ids in the product's own records, and the
  product's ids on the traces.
- **Redaction at the source.** Strip or mask personal data before spans
  leave the process.
- **Retention and cost planned.** Trace volume grows with traffic; decide
  what is kept, where, and for how long.
- **Budget the backend, not just the volume.** A self-hosted stack can cost
  more to run than the data it holds: SREF's Langfuse used about 4 GB of
  memory at idle to hold 1.4 MB of traces
  ([observability_tools.md](observability_tools.md) §5).

---

## 6. Grades in the tools

**SREF.** After a run, each case attempt's verdict is sent to every tool as
its native grade (`verdict` and `pass`), read from the run record and safe
to re-send. A wrong answer then shows as a fail next to a green trace status.

**In production:**
- **Grades flow automatically,** from the eval pipeline and from online
  evaluators, not by a manual step.
- **One vocabulary.** Grade names and scales are defined once and used by
  every evaluator and dashboard.
- **Dashboards on grades, not just traces.** Pass rate by slice, by model,
  by prompt version, over time.
- **Alerts on grade drops,** the same way latency or errors alert.

---

## 7. Human labelling

**SREF.** Each failure gets one categorical label, `fault` (agent, question,
gold, harness), plus a one-line note on the first thing that went wrong. A
model drafts every label; a person reviews a sample. All three tools were
tried; Langfuse was chosen because it keeps a model's draft and a person's
review apart. Labels are joined back to the run record by trace id.

**In production:**
- **A written labelling guide,** with definitions and worked examples for
  every label value, so labels mean the same thing across people and time.
- **Two reviewers on a sample,** with their agreement measured. Low
  agreement means the guide, not the reviewers, needs work.
- **Model drafts are fine; provenance is not optional.** Record who made
  every label, and measure how often reviewers change the draft.
- **Review every failure in the areas that matter,** and a sample elsewhere.
- **Queues and ownership.** Labelling work is assigned, tracked and finished,
  not done ad hoc.

---

## 8. Context and leakage

**SREF.** The agent sees a schema description that states facts about the
data (grain, encodings, observed values, coverage) and never how to query.
Nothing from the trap catalog, gold SQL or eval design reaches the prompt,
and tests enforce it.

**In production:**
- **Separate what the system knows from what the eval knows.** Answer keys,
  rubrics and failure catalogs stay out of prompts, retrieval indexes and
  training data.
- **Automated leak checks** on every prompt, retrieval and fine-tuning
  change.
- **Watch for indirect leaks.** Few-shot examples copied from the eval set,
  or eval traces reused as prompt examples, contaminate every later score.

---

## 9. The fix loop

**SREF.** The failure taxonomy pointed at one type; one generic fix (retry
a failed query with the database's error) was added as a run setting, run
against the same cases and attempts, and compared attempt by attempt. It
fixed none of 8 crashes and turned two into silent wrong answers
([findings.md](findings.md) article 10).

**In production:**
- **Pick the fix from the taxonomy,** aimed at the largest failure type the
  team can act on, with the expected effect written down before the run.
- **Compare like for like.** Same cases, same attempts, one setting changed;
  check that everything the fix should not touch came back unchanged.
- **Read where the fix lands, by failure type.** Fixed, unchanged, or moved
  to a worse type (loud to silent) — not just the headline score.
- **Judge the difference against the noise floor** (§4) before calling a
  fix a win.
- **Keep negative results.** A fix that did not work, and why, saves the
  next person the run.

---

## 10. An LLM judge

**SREF.** The agent writes a prose answer from its rows; `gemma4:12b`
grades faithfulness only, from the question, the rows and the answer, never
gold. A person labelled 11 answers blind, including 2 planted unfaithful
ones; the judge agreed on 10, with one lenient miss
([findings.md](findings.md) article 11). Verdicts go to Langfuse as
`faithful_judge` next to `faithful_human`.

**In production:**
- **Give the judge one narrow question.** Faithfulness, or relevance, or
  tone — each its own judge with its own rules, not one "is it good?".
- **Write the rules down first,** from a labelling guide, and state them in
  the judge's prompt in the same words people label against.
- **Validate on a held-out set.** Write the rules from one labelled set;
  measure the judge on another. Hundreds of items, not eleven.
- **Report agreement by direction,** lenient against strict, and per
  category; set the bar by what a lenient miss costs.
- **Plant known failures** so a judge that passes everything is caught.
- **Treat the judge's output as untrusted.** Leave room for reasoning in
  the token budget, parse defensively, and count unreadable verdicts
  separately.
- **Re-validate on every change** to the judge's model, prompt or settings,
  and spot-check its verdicts on live traffic over time.
- **Keep the judge's settings as config,** versioned like the agent's.

---

## 11. Experiments: comparing two setups

**SREF.** The gold cases are a Langfuse dataset (`sref-gold`): questions
only, since gold runs at eval time. A run flag records each attempt as a
Langfuse experiment, one trace per case, carrying the gold rows from that
run as expected output and the agent's rows as output. qwen and
`gpt-6.1-sol` ran the same 10 cases × 3 attempts, identical except where the
model forced a difference (temperature, output cap), and were read side by
side in Langfuse's compare view: 5/30 against 22/30, ↗ 6 ↘ 0 on attempt 1
([findings.md](findings.md) article 12). The run record stays the source;
the experiment is a view over it.

**In production:**
- **One dataset, many experiments.** The test set lives in one versioned
  place, and every candidate (model, prompt, retrieval setting) runs
  against the same version of it.
- **Change one thing, and record what else changed.** Some settings are
  forced by the model (a fixed temperature, a different token cap); list
  them with the result, since they are part of what was compared.
- **Compare per item, not just per score.** Which items improved, which
  regressed, which changed verdict. A gain on some items can hide losses
  on others.
- **Compare against the noise.** Repeat runs and report the spread; a
  candidate at higher temperature varies more between runs than one at 0.
- **Track cost and latency as results,** beside quality: per item, per
  experiment, with cached input priced separately. A better score at ten
  times the cost is a different decision.
- **Keep the comparison reproducible** from the team's own record of the run,
  not only the tool's view of it; tools change their data model (Langfuse
  v4 moved experiments from a link API to trace attributes).

---

## 12. A regression gate

**SREF.** `evals/gate.py` compares a run with a pinned baseline run of the
same config, from the run record alone, and exits non-zero when either rule
fails: the pass rate falls more than 10 points below the baseline (the
noise margin, about 3 of 30 attempts), or a case that passed every baseline
attempt fails every attempt now. It refuses to compare runs on different
snapshots, cases or attempt counts, and reports which settings changed.
Run by hand; the baselines are included in `runs/`.

**In production:**
- **Run it automatically** on every change to a prompt, model, tool or
  retrieval setting, in CI, and block the merge or release on a failure.
- **Set the margin from measured noise,** and narrow it as the test set
  grows. A margin narrower than the noise fails good changes; a wider one
  passes real regressions.
- **Gate per slice and per critical case,** not only on the overall score:
  a must-pass list (safety refusals, top user intents) fails the build on
  any single loss.
- **Keep baselines durable and reviewed.** Store the baseline run where CI
  can read it, and move the baseline only on purpose, when a change is
  accepted as the new normal.
- **Gate cost and latency too,** with their own budgets, so a quality-
  neutral change that doubles cost is caught.
- **Make failures readable.** The gate names the cases and settings behind
  a failure, so the person blocked can act without re-running anything.

---

## 13. Reporting results

**SREF.** The eval report's tables are generated from pinned runs: a script
reads the run record, computes each metric from one written definition with
its denominator ([eval_plan.md](eval_plan.md) §7), and fills the tables into
a hand-written narrative. The report names the runs and the commit behind
its numbers, and an unknown placeholder stops the build.

**In production:**
- **Build reports from the run store, never retype a number.** A figure
  copied by hand drifts from its source the first time either changes.
- **Define every metric once, in code,** versioned with the harness, and
  use that definition in every report and dashboard.
- **Name the evidence.** Every report states the runs, data version and
  code version behind it, so any number can be traced and re-computed.
- **Regenerate on a schedule or on every eval run,** not assembled by hand
  for each review.
- **Keep the narrative separate from the numbers,** so new runs update the
  figures and leave the argument to be re-read.

---

## Not exercised in SREF

Parts of a production eval that this project does not cover:

- **Online evaluation.** Evaluators running on a sample of live traffic,
  not only on a fixed test set.
- **User feedback.** Thumbs, corrections and escalations, captured and
  joined to traces.
- **Drift monitoring.** Changes in inputs, outputs or scores over time.
- **Cost and latency budgets.** Targets per request, checked in every run.
  SREF records both (§11) but sets no budget and gates neither.
- **Safety and adversarial testing.** Prompt injection, harmful requests,
  data exfiltration through the agent's tools.
- **Multi-turn and multi-step agents.** Evals over conversations and tool
  sequences, not single questions.
- **Eval ownership.** Who maintains the gold set, signs off on the judge,
  and decides what score blocks a release.
