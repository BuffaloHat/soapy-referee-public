# SREF — Findings

What building and running this harness has taught. Each finding leads with
what was learned, then how it was found, then what changed because of it.

When a later run changes a finding, the article is updated and says what
changed and when. Case-writing rules are in
[gold_cases.md](gold_cases.md).

---

## Headlines

1. **A deterministic model is not a stable one.** The same prompt always gets
   the same answer, but moving one unrelated sentence can change it. At ten
   cases, a one-case difference between two setups is noise.
2. **Documentation fixes what the model can't know, not what it doesn't pay
   attention to.** Explaining the season encoding taught the model to use it,
   but it still gets the details wrong about two times in three. Facts written
   plainly in its prompt get ignored outright.
3. **A single pass rate hides real changes.** The model went from honestly
   declining questions to attempting them, and the score didn't move.
4. **A clear question beats a smarter model.** Rewording one ambiguous question
   fixed a case that no amount of schema documentation had.
5. **The instrument fails silently too.** Several harness bugs would have
   produced clean-looking, wrong results. Each was found by running against
   real output, not by unit tests.
6. **Describe the data, not the test.** Documenting only the columns the
   gold cases use would turn the schema docs into a pointer to the answers.
7. **Tracing tools show what the code did, not whether it was right.** A
   silently wrong answer looks healthy in every tool. And what each tool
   shows depends on how the spans are wired as much as on the tool.
8. **When a model drafts labels, the tool has to keep its draft apart from
   the person's review.** On the same draft, phx overwrote it, fuse kept
   both, and smith credited the model's draft to the person. fuse is now
   SREF's only labelling tool.
9. **Half the failures are a coded value gotten wrong, even with the codes
   documented.** Every one of 25 failures was the agent's; none was the
   question's, the gold answer's or the harness's.
10. **Showing the model its error fixed the error, not the query.** One
    retry on 8 crashed attempts: none fixed, six crashed on the next error,
    two turned into silent wrong answers. Pass rate unchanged at 5/30.
11. **An LLM judge agreed with a person on 10 of 11 answers, and its one
    miss was the costly kind.** It caught a rounding slip the person first
    missed, and passed an invented claim it explained away.
12. **A stronger model tests the gold set as well as itself.** `gpt-6.1-sol`
    passed 22 of 30 to qwen's 5. Only half its 8 misses were wrong
    answers: three were right in a different unit and one answered the
    question as worded. qwen never got far enough to expose either.

---

## 1. A deterministic model is not a stable one
*2026-09-30, measured 2026-10-01*

At `temperature: 0` with a fixed seed, the agent is perfectly reproducible:
the same prompt returns the same SQL, byte for byte, across a week and an
Ollama restart. That looked like a solid foundation for comparing setups. It
isn't, because a small change to the prompt that has nothing to do with the
question can flip the answer.

```
before: WHERE season_id = '21996' AND wl_home = 'W'    exact
after:  WHERE season_id = '21997' AND wl_home = 'W'    wrong
```

Those two queries for `best-home-record-1996-97` differ by one digit. The only
change between them was a 75-token sentence about home/away columns, added to
three unrelated table descriptions.

**How it was measured.** If one small change can flip a case, how often does
that happen? Rerunning the same config wouldn't answer that, because identical
prompts give identical answers and k reruns would just record one result k
times. So each case ran three times, with the order of tables in the schema
shuffled on attempts 2 and 3. That's the same facts in a different order, like
reordering a FROM list.

| arm | verdict changed | flipped pass/fail | SQL changed | passes per attempt |
|---|---|---|---|---|
| bare columns | 3 of 10 | 0 of 10 | 8 of 10 | 1 · 1 · 1 |
| documented schema | 5 of 10 | 1 of 10 | 10 of 10 | 2 · 1 · 2 |

Reordering the tables rewrote almost every query. Most verdicts held anyway,
but only because most cases fail every way the model tries them. The one
pass/fail flip was the same case and the same `'21997'` slip as before, from
an unrelated change.

**What it means.** The noise floor at this size is about one case in ten. An
earlier report of "documented schema 2/10 beats bare columns 1/10" sits
inside it and isn't a result. The direction held on every attempt, but the gap
is too small to call.

**What changed.**
- Configs gained `perturbation: table_order`.
- A config that asks for repeats at temperature 0 without a perturbation is
  now rejected, since it would measure nothing.
- Each run records the table order every attempt saw, plus a per-case
  stability summary.
- Comparisons now use the three-attempt configs (`local-columns-r3`,
  `local-docs-r3`).

---

## 2. Documentation fixes what the model can't know, not what it doesn't pay attention to
*2026-09-23, revised 2026-10-01*

The experiment compares two arms: the bare column list against documented
schema (grain, encodings, observed values, coverage). The documentation clearly
did something, but not what was expected.

**It taught the encoding, but not care in applying it.** Seasons are stored as
codes: `'21996'` means regular season (`2`), starting in 1996. Nothing in the
column list reveals that.
- **Bare columns:** the model never wrote a valid code, 0 times in 18 tries.
- **Documented schema:** it reached for the code format 15 times in 18, but
  10 of those 15 were wrong in a different way each time: `'21997'` (end year),
  `'22082'` (1982 mangled), `'21819'` (both years run together), `'42022'`
  (playoffs). On one prompt it looked like a clean 4-for-4. Across three
  orderings, it got the right code 5 times in 18.

**Some facts written plainly in the prompt were ignored outright:**
- **All-Star games** are spelled two ways in `season_type`. The documented
  schema lists both, side by side. The model filtered on one spelling and
  missed the other 63 rows.
- **`team_info_common`** is documented as "Empty. The table has no rows."
  The model built its whole `biggest-improvements` answer on that table.
- **The wide layout** (each team's stats split across `*_home` and `*_away`
  columns) causes five of nine failures. The model answers from the home
  columns alone. The table description already said so. A second, sharper
  wording ("a team appears as `team_id_home` in its home games and
  `team_id_away` in its away games") improved none of the five, so it was
  reverted.

**What it means.** Documentation makes a fact knowable. It doesn't make the
model attend to it. The remaining levers are general output conventions in the
prompt, or a different model. Anything derived from the traps themselves
would leak the answer key into the agent. The documented arm is also 5×
longer than the bare one, so part of its effect may be prompt length; a third
arm, statistics without the hand-written prose, would separate the two.

---

## 3. A single pass rate hides real changes
*2026-09-23*

With bare columns, the model declined both All-Star questions: "the database
does not contain information about NBA All-Star Games." That's a fair answer
when nothing says All-Star games are in the data. With documented schema it
attempts them. It still gets them wrong, but it has moved from "can't see the
data" to "can't query it correctly."

Pass rate: zero in both arms, no change. The verdict breakdown, which moved
from refusal to wrong answer, showed real progress. That's the argument for
reporting the full distribution of verdicts rather than one score.

---

## 4. A clear question beats a smarter model
*2026-09-30*

`best-home-record-1996-97` asked "which team had the best home record", but
gold returned both the team and its win count. The agent answered `CHI`, the
right team, and scored `wrong` for leaving out a column nobody asked for.
Once the question asked for both, the agent changed its query and scored
`exact`. Same model, same settings, same schema.

The prediction was that the rewording wouldn't help. It did. The agent was answering
the question as asked.

**The rejected fix.** Narrowing gold to return only the team would have
produced the same score, by making the case easier. The rule kept: *the
number should move because the agent got better, not because the target
did.* This became gold rule 12: the question must ask for every column gold
returns.

---

## 5. The instrument fails silently too
*2026-09-22*

Every one of these would have produced a clean run record with wrong numbers
in it.

- **Ollama truncates silently.** If the prompt is too long, it drops
  the start (the schema) and answers anyway. Now the harness sends
  `truncate: false`, so an oversized prompt is an error.
- **The refusal parser read too much.** It searched the whole reply for
  `CANNOT ANSWER`, so SQL with that phrase in a comment would have scored as a
  decline. Now only the first line counts. This was found on the first real
  run.
- **Token estimates were wrong.** The usual four-characters-per-token rule of
  thumb understated the documented schema by 46% (11,578 estimated, 16,926
  actual). Schema text is identifiers and numbers, which tokenize far worse
  than prose. The context-headroom test now uses the measured ratio, with a
  margin.
- **The sampler showed only low values.** The schema profiler sorted its sample
  and kept the first three rows, so `season_id` showed three preseason codes
  and hid the regular-season encoding. The documented arm would have concealed
  exactly what it was meant to show. It now lists every value for small
  columns, and min, max and a median for the rest.

---

## 6. Writing schema docs without leaking the answers
*2026-09-22*

The agent's schema docs must state facts, never hint at how to query. Two
rules came out of writing them.

- **Don't document only what the cases use.** That makes the *shape* of the
  docs a pointer to the answer key ("described means relevant"), and scores
  would rise for the wrong reason. A column gets a description only when its
  meaning can't be read from its name and values.
- **Only describe a coded column when another column backs up the reading.**
  `eventmsgtype`, `person1type` and the score ordering could each be decoded
  only because another column in the same row could have contradicted the
  reading. Six columns had no such check and were left blank on purpose. A
  confident wrong description is worse than none.

---

## 7. Tracing tools show what the code did, not whether it was right
*2026-10-02, fixes verified 2026-10-05*

`lakers-ppg-2022-23` asks for the Lakers' points per game. The agent averaged
home games only, filtered on a team name that matches no rows, and returned a
single row holding NULL. No error. In smith, fuse and phx alike the trace
looks healthy: every step ran, nothing turned red. Trace status means "did
the code throw", not "was the answer right". Correctness exists only in
SREF's comparator, and the silent failures SREF exists to measure are
exactly the ones tracing alone cannot see. To show them, the verdict has to
be sent to each tool as its native grade. SREF does since 2026-10-05
(`evals/scores.py`): a `verdict` and a `pass` grade on every trace, and a
wrong answer now shows `pass 0` next to its green status.

**How it was found.** One full traced run, walked through all three tools
view by view from screenshots, following a clean pass, a silent failure and
an agent-SQL error. The walkthrough and the side-by-side comparison are in
[observability_tools.md](observability_tools.md) §4–5.

**Second finding: instrument once, but wire for the least forgiving tool.**
One set of OpenTelemetry + OpenInference spans reached all three tools, and
each showed a different subset of it. phx showed everything as sent. smith
dropped attributes it did not recognise and the system prompt. The sharpest
case: SREF *catches* a failing agent query on purpose, because it is a
verdict, not a crash. smith marks errors only from recorded exceptions, so
every failed case looked green on its first screen. What a tool can show is
decided by the wiring as much as by the tool.

**What changed.** The walkthrough produced 14 wiring lessons. Thirteen were
fixed and verified with a re-run in all three tools; one is a tool limit: errors recorded
where the outcome is decided, both prompt messages visible, one thread per
run, gold and agent queries named apart, the join keys on every step, and an
environment and release on every trace. The spec, the lessons and a
translation matrix of each tool's vocabulary are in
[observability_tools.md](observability_tools.md) §5–6.

**What it means.**

- **The run record stays the source of truth.** No tool shows correctness,
  each shows a different subset of what was sent, and smith's free tier
  keeps traces for 14 days.
- **Use phx to check the instrumentation.** If a field is missing there, it
  was never sent.
- **Put everything worth seeing in `metadata`.** It is the one field every
  tool keeps.

---

## 8. Whose label is it?
*2026-10-09, decision 2026-10-12*

Labelling failures by hand is slow, so Phase 5 used
pre-labelling: a model drafts each failure's label (`fault`, plus a one-line
note) and a person reviews it. That only works if the record shows which
labels the person checked and which they changed. Given the same draft, the
three tools did three different things. **phx** opened the draft for editing
and overwrote it on save, so nothing records whether the reviewer agreed.
**fuse** left the draft untouched and stored the review as a second label
beside it. **smith** showed the model's draft as created by the owner of
the API key that sent it, and offered no way to review it at all.

**How it was found.** A two-item bake-off in each tool: one failure labelled
from scratch, one drafted through the API and reviewed in the UI. Setup took
about two minutes per tool; the differences only showed up when a draft was
involved. Details in [observability_tools.md](observability_tools.md) §5,
*Labelling by hand*.

**Second finding: the note is second-class everywhere.** The label shows on
the trace; the note behind it needs a hover or a click in every tool, and
smith's quick flow has no field for one. Only fuse's Scores page lists the
notes together. Two of the three tools also save the moment a value is
picked, and phx, which has a save button, still let a mis-click through.

**What changed.** fuse is SREF's only labelling tool from 2026-10-12. It is
the one tool that keeps a model's draft and a person's review apart, its
Scores page lists every label with its note, and it runs locally with no
retention limit. Its annotation queue carried the remaining labels: a
model drafted all of them and a person reviewed a sample (article 9).
Labels are read back into the run record by trace id, with who made them,
so the agreement rate between draft and review is SREF's to compute,
whatever the tool shows.

**The other two.** Traces still go to all three; only labelling moves.
**phx is second:** the simplest to label in, but saving a review overwrites
the draft. Use it to check the instrumentation, since it shows everything as
sent, and to read LLM calls. **smith** is the polished hosted viewer for
browsing traces, not for labels: it cannot review a draft and shows a
model's label as the person's.

**What it means.**

- **Record who made every label, in the eval's own record.** No tool can be
  trusted to keep the difference between a model's label and a person's.
- **Check how a tool shows provenance before trusting its label counts.**

---

## 9. How the agent fails
*2026-10-13*

Every one of the 25 failed attempts in run `20261005-211049-local-docs-r3`
(10 cases × 3 attempts, `docs` schema) was the agent's fault: no question
was ambiguous enough, no gold answer wrong, no harness bug. Grouped by the
first thing that went wrong, they fall into four types:

| Type | Count | What happened |
|---|---|---|
| **A. A coded value gotten wrong** | 12 (48%) | Wrong season code, 7 (`'21997'` for 1996-97, the playoff code `'42022'` for the regular season, `'22%'` for one season) · one All-Star spelling of two, 4 · `'Lakers'` for `'Los Angeles Lakers'`, 1 |
| **B. A column that isn't there** | 7 (28%) | `line_score.season_id`, `team.team_name`, `game.team_id`, `pct`, `w`, `l` (6, all crashed) · a season code applied to a column holding plain years (1) |
| **C. The question misread** | 3 (12%) | "Who scored the most points" answered with a team's score, every attempt |
| **D. The wrong shape of answer** | 3 (12%) | Grouped by matchup instead of by team · duplicates counted twice · built on the empty table |

Type A is the lesson. The season encoding and both All-Star spellings are
in the agent's schema documentation, and the agent still got them wrong in
12 of 25 failures. That is article 2 at scale: documentation fixes what the
model cannot know, not how carefully it applies what it was told.

Eight attempts crashed (six of type B, two of type C); the other 17 ran and
returned a wrong answer without complaint. Two in three failures were
silent.

**How it was labelled.** Each failure got a `fault` label and a one-line
note on the first thing that went wrong. A model drafted every label, with
the database lookups behind each note; a person reviewed a sample in Langfuse.
The types were then grouped from the notes, not decided in advance. One row
per failure, with its type, is in the run folder as `taxonomy.jsonl`.

**What it means.**

- **Fix by type, not by case.** Type A needs the agent to apply what it was
  told; type B is a crash with an error message the agent never saw. They
  call for different fixes.
- **Crashes are the cheap failures.** A query that fails says so; the 17
  silent ones are what the comparator exists to catch.

---

## 10. A retry fixes the error it is shown, not the query
*2026-10-13*

The most common generic fix for a text-to-SQL agent is to send a failed
query back to the model with the database's error and let it try again. On
SREF it fixed nothing. Run `20261013-183745-local-docs-r3-retry` is
`local-docs-r3` with one retry; the 22 attempts that did not crash came back
byte-identical, so every difference is the retry's. Of the 8 attempts that
crashed, none was fixed. Six crashed again; two now return a wrong answer
without complaint. The pass rate stayed at 5/30.

Every retry fixed exactly the error it was shown and then hit the next one.
`team.team_name` became `team.full_name`, as DuckDB's error suggested, and
the query then failed on `game.pct`, a column that exists nowhere. The
database reports one error at a time; these queries had two or three. Behind
the errors sits the real failure from article 9: the agent writes against a
schema it imagines, and one corrected column does not change that.

**How it was measured.** The fix is a run setting (`sql_retries`), not a
code branch, so the before and after arms run the same code. Attempts were
compared one to one against run `20261005-211049-local-docs-r3`, with the
failure types from article 9.

**What it means.**

- **Measure where a fix lands, not just the score.** "Errors down from 8 to
  6" sounds like progress; two of those were loud failures turned silent,
  which is worse.
- **Fix the failure type, not its symptom.** A crash on a missing column is
  the symptom; writing against an imagined schema is the type.
- **A negative result is a result.** The fix was cheap to try because it was
  a setting, and the eval showed in one run that it does not help here.

---

## 11. Checking the judge
*2026-10-13*

The agent now writes a one- or two-sentence answer from its rows, and an
LLM judge (`gemma4:12b`) grades whether that answer says only what the rows
say. Faithfulness only: an answer that accurately reports wrong rows is
faithful, and correctness stays with the comparator. A judge is a model, so
before its verdicts count they were compared with a person's labels on the
same 11 answers, 9 written by the agent and 2 deliberately falsified.

| | Count |
|---|---|
| Judge and person agree | 10 of 11 |
| Judge too lenient (passed an answer the person failed) | 1 |
| Judge too strict | 0 |

The judge caught both planted answers (a changed number, an invented claim)
and one slip the person had first labelled faithful: the rows held
259.796875 and the answer said "260.0 when rounded to one decimal place". Its
miss was the opposite kind. The rows held a team's 144 points; the answer
credited them to "the Chicago Bulls' player", and the judge passed it as
"a player on the Chicago Bulls as shown in the result". It read meaning
into the rows that is not there. A lenient miss is the costly one: an
unfaithful answer gets through.

**How it was measured.** Run `20261013-193133-local-docs-r3-answer` adds a
prose answer (`write_answer`) to `local-docs-r3`; the SQL verdicts came back
unchanged. The person labelled the 11 answers in a blind set before the
judge ran; one label was changed on review, and both versions are kept in
the run folder (`judge_set.jsonl`). The judge's prompt states six rules, the
labelling guide written while labelling: claims may restate the question,
numbers must match (correct rounding allowed), empty or NULL may be "no
data", known abbreviations may be expanded, correct arithmetic counts,
anything else unsupported fails.

**Second finding: the judge's output needs the same care as the agent's.**
On the first pass one verdict came back empty: gemma reasons out of sight
before replying, a hard item needed 2,140 tokens, and the cap was 1,024. On
the retry it answered `Line 1: UNFAITHFUL`, echoing the prompt, which the
parser did not read. Either would have skewed the count without notice.
Unreadable verdicts are now counted on their own, never as passes.

**What it means.**

- **10 of 11 is optimistic.** The rules came from labelling these same
  answers, so the judge was checked on the set its rules were written from.
  A fair estimate needs a separate set.
- **Know which way the judge errs.** Agreement alone hides it; one lenient
  miss matters more than one strict one.
- **Plant known failures.** Without the two planted answers, a judge that
  passed everything would still have agreed on most items.

---

## 12. A stronger model tests the gold set as well as itself
*2026-10-16*

The same 10 cases, three attempts each, run as fuse experiments against
qwen and one OpenAI model, `gpt-6.1-sol`:

| | qwen2.5-coder:14b | gpt-6.1-sol |
|---|---|---|
| Passing | 5/30 | 22/30 |
| Errors (SQL that did not run) | 8 | 0 |
| Silently wrong (`wrong` + `subset`) | 17 | 8 |
| Verdict changed between attempts | 5 of 10 cases | 3 of 10 |
| Cost | $0, local | ~$0.78 |

The gap is no surprise. What sol's 8 misses turned out to be is. Every
one of qwen's failures in article 9 was the agent's. Only half of sol's are:

| Case | Attempts | What happened | Whose miss |
|---|---|---|---|
| `all-star-average-score` | 2 | 259.8 against 260.0: counted the games stored under both spellings twice. The trap the case was written for | the model's |
| `fewest-points-in-a-quarter-2018-19` | 2 | 8 against 7. Joined `line_score` to `game` on the team ids, and the two tables disagree on which team was at home, so the 7-point game dropped out | the model's, on an undocumented data fact |
| `biggest-improvements` | 3 | The right five team-seasons in the right order, given in percentage points (51.22) where gold uses a fraction (0.512). The comparator finds no matching column | the instrument's |
| `attendance-leader-2014-15` | 1 | The right leader and the right margin. It left out the runner-up's name, which the gold requires and the question doesn't clearly ask for | the gold's, or the question's |

Read case by case, sol answered correctly on 25 or 26 of 30, not 22.
qwen never reached either problem: its errors and wrong joins came first.

**The data fact.** For the 7-point game, `line_score` has Chicago away at
Houston, as published; `game` has the sides swapped. Joined on `game_id`,
27,237 of 58,149 rows disagree on home and away; in the 2018-19 regular
season, 541 of 1,125. Nothing in the schema docs or the trap catalog said
so at the time of the run; the data dictionary has carried it as trap 11
since 2026-10-20. A join that also matches the team ids looks careful and silently loses
close to half the games.

**How it was measured.** Eval runs `20261015-220144-local-docs-r3` and
`20261015-215958-openai-docs-r3`, identical except where the model forced a
difference: sol accepts only temperature 1, and its output cap is 4096
because hidden reasoning shares it. Each attempt is one fuse experiment on
dataset `sref-gold`, with the gold rows from the run as expected output;
fuse's compare view shows qwen #1 against sol #1 as ↗ 6 ↘ 0. The misses
were read from the run record: gold and agent rows side by side, plus the
SQL.

**What it means.**

- **A better model moves the failures, from the agent to the gold set.**
  Against a weak model every miss is the model's. Against a strong one the
  gold, the contract and the question get tested too, so error analysis
  has to start again.
- **Read the misses before quoting the score.** "22 of 30" is the strict
  number. The case-by-case reading moves it by three or four, more than
  the noise margin the regression gate allows.
- **The gold set had a trap it didn't know about.** The home/away
  disagreement is a data fact that should have been found in Phase 0. The
  stronger model found it by writing the more careful-looking join.
- **Temperature 1 has a cost.** sol scored 8, 8 and 6 across its three
  attempts with nothing changed. qwen at temperature 0 moved by one.

**Questions the run raised.** Left unfixed on purpose: the cases stay as
they are, as worked examples. For each, the question, the fix a team would
make, and the lesson.

1. **A right answer in another unit** (`biggest-improvements`).
   - *Question:* is an improvement of 51.22 percentage points the same
     answer as 0.512?
   - *Fix:* decide it in the case. Either the question names the form ("as
     a fraction, to three decimals"), or the contract accepts both and the
     comparator allows a scale factor of 100.
   - *Lesson:* a number's format is part of the question. State units,
     scale and rounding, and decide before the run which forms pass.
2. **More columns than the question asked for** (`attendance-leader-2014-15`).
   - *Question:* does "how far ahead of the second-highest team" require
     naming that team?
   - *Fix:* reword the question to ask for both teams, or drop the
     runner-up from the gold's required columns.
   - *Lesson:* the gold's columns are the pass condition. Every required
     column should trace back to words in the question (article 4).
3. **A data fact nobody had written down** (`fewest-points-in-a-quarter-2018-19`).
   - *Question:* which table is right about home and away, and should the
     agent be told?
   - *Fix:* check the disagreement against published results (`line_score`
     matches for the 7-point game), record it in the data dictionary and
     the trap catalog, and state it in the schema docs as a plain fact,
     not as advice on how to query around it (no-leak rule). Done for the
     data dictionary (trap 11, 2026-10-20); the schema docs and the case
     stay as they are.
   - *Lesson:* profile tables against each other, not only one at a time.
     Two tables describing the same game can disagree, and the model's
     documentation is only as complete as that profiling.

---

## External context

- **Published benchmarks have shaky gold.** A CIDR 2026 paper found annotation
  errors in 52.8% of BIRD Mini-Dev and 66.1% of Spider 2.0-Snow. Verifying the
  gold is the expensive part of an eval harness, and the part least worth
  outsourcing. That's why every SREF case carries an independent check query.
- **Executing gold is the field standard.** Spider and BIRD score on execution
  accuracy, meaning whether the result rows match, not SQL text similarity.
