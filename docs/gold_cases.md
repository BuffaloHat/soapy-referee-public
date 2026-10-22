# SREF — Gold Cases
**Last updated:** 2026-10-21

> How gold cases are written, and what the set covers. The case files live in
> `evals/cases/`; the loader is `evals/cases.py`. This doc is eval-side only —
> never agent context.

---

## 1. What a case is

One YAML file per case, named after its `id`.

| Field | Purpose |
|---|---|
| `id`, `question` | The question exactly as the agent receives it |
| `tier` | `baseline` · `trap` · `hard` · `refusal` |
| `traps` | Which trap(s) from the data dictionary the case exercises |
| `expects` | `rows`, or `refusal` for questions the data cannot answer |
| `cardinality` | How many rows the answer has. Catches fan-out |
| `gold_sql` | The answer as a program, run at eval time |
| `check_sql` | The same answer by a different route. Must agree with gold |
| `contract` | How to compare: `ordered`, `decimals`, `teams`, `players`, `seasons`, … |
| `provenance` | Why the answer is trusted: a published record, or two agreeing queries |
| `notes` | The failure the case exists to catch, and any defense it relies on |

`tests/test_cases.py` rejects any case whose gold SQL fails, returns the wrong
number of rows, or disagrees with its check query.

---

## 2. Writing rules

Each rule was learned from a specific case. Follow all of them for new cases.

| # | Rule | Why | Learned from |
|---|---|---|---|
| 1 | **Ask for enough rows to reach the trap.** | The naive query can be right at the top and wrong further down. Top 1 passes; top 5 fails. | `biggest-improvements` |
| 2 | **Pin the precision in the question when the trap's effect is smaller than rounding.** | 259.8 and 260.0 are the same at whole points. The contract's `decimals` must match what the question asks for. | `all-star-average-score` |
| 3 | **Pick inputs where the answer doesn't hinge on missing rows.** | Over a partial table, a close margin is decided by which rows happen to be absent — not by the agent. | `attendance-leader-2014-15` (2022-23 was 12 fans apart) |
| 4 | **Match "previous season" by year, never by row order.** | Seven regular seasons are missing; row order pairs 2013-14 with 2011-12. | `biggest-improvements` |
| 5 | **Identify entities by ID; declare them in the contract.** | Gold returns `team_id`; the agent says "Boston Celtics". `teams:` / `players:` / `seasons:` let resolution make them comparable. | `biggest-improvements` |
| 6 | **`check_sql` must be a truly independent route — and must not cross a coverage gap.** | A second query that shares the first one's mistake proves nothing. Joining `line_score` silently changed an answer. | `test_gold_equivalence.py` |
| 7 | **Cut questions whose answer depends on an unstated definition.** | If two reasonable readings give different answers, the case grades the reading, not the agent. Sample vs. population stddev; mean of ratios vs. ratio of sums. | Question review, 2026-09-21 |
| 8 | **Say what the provenance actually is.** | A published record and "two queries agree" are different strengths of evidence. Never imply a published figure that wasn't checked. | `all-star-average-score` |
| 9 | **A refusal case must be unanswerable, not just hard.** | If the answer is derivable with effort, an agent that attempts it isn't clearly wrong. Check the schema: the data category must be absent. | `player-salary-refusal` (usage rate is derivable from play-by-play) |
| 10 | **Verify the trap through DuckDB, not from the dictionary's description.** | The harness runs every query through DuckDB, which casts and errors differently from SQLite. A trap that is silent in SQLite can be loud — or absent — here. | `fewest-points-in-a-quarter-2018-19` (trap 5 turned out not to be silent) |
| 11 | **No tie at the cutoff of a top-N question.** | If row N ties row N+1, `LIMIT N` picks one arbitrarily and gold is not deterministic. Order by the unrounded value; `test_no_tie_at_the_cutoff` enforces it. | `top-rebounding-teams-1982-83` (BOS and CHI tied at 46.5 for third) |
| 12 | **The question must ask for every column gold returns.** | Otherwise the case grades a guess about output shape rather than the answer. The agent returned the right team and scored `wrong` for omitting a win count nobody asked it for. | `best-home-record-1996-97` (first agent run, 2026-09-22) |
| 13 | **State the answer's form: units and scale as well as rounding.** | A right answer in another unit finds no matching column and scores `wrong`: 51.22 percentage points against 0.512. | `biggest-improvements` (2026-10-15) |

---

## 3. Case index

| Case | Tier | Traps | Defense relied on | Provenance |
|---|---|---|---|---|
| `best-home-record-1996-97` | baseline | season-encoding | Rule 12 | Published (CHI 39-2) |
| `lakers-ppg-2022-23` | trap | home-away-split, season-encoding | `decimals: 1` | Published (117.2) |
| `biggest-improvements` | trap | missing-seasons, home-away-split, season-encoding | Rules 1, 4, 5 | Published #1 (2007-08 Celtics) |
| `attendance-leader-2014-15` | trap | coverage-gap, season-encoding | Rule 3 | Published leader (CHI) |
| `all-star-game-count` | trap | all-star-spelling, duplicate-rows | — | Published (72 games) |
| `all-star-average-score` | trap | all-star-spelling, duplicate-rows | Rule 2 | Two queries agree |
| `top-rebounding-teams-1982-83` | trap | era-nulls, home-away-split, season-encoding | Rules 1, 2, 5, 11 | Two queries agree; leader consistent with history |
| `player-salary-refusal` | refusal | not-in-data | Rule 9 | Schema checked: no salary data anywhere |
| `single-game-scoring-high-2015-16` | hard | play-by-play-only, duplicate-rows | Rule 5 (`players:`), Rule 6 | Published (Kobe 60); top five reproduced |
| `fewest-points-in-a-quarter-2018-19` | trap | mixed-storage-types, home-away-split | Rules 3, 10 | Two queries agree; quarters sum to the final score |

Two cases are kept as worked examples of rules they break:
`biggest-improvements` (rule 13) and `attendance-leader-2014-15` (rule 12).
See findings article 12.

### Coverage against the trap catalog

| Data dictionary trap | Covered by |
|---|---|
| 1. Home/away denormalization | `lakers-ppg-2022-23`, `biggest-improvements`, `top-rebounding-teams-1982-83` |
| 2. `All Star` / `All-Star` | `all-star-game-count`, `all-star-average-score` |
| 3. Era-dependent NULLs | `top-rebounding-teams-1982-83` |
| 4. Coverage gaps between tables | `attendance-leader-2014-15` |
| 5. Type mismatch across join keys | Not silent through DuckDB — no case (see data dictionary) |
| 6. Mixed storage types | `fewest-points-in-a-quarter-2018-19` |
| 7. Dimension tables that don't cover the facts | — |
| 8. The empty table | — |
| 9. Four overlapping game tables | — |
| 10. Missing regular seasons | `biggest-improvements` |
| 11. `game` vs `line_score` home/away | `fewest-points-in-a-quarter-2018-19`, found there by a model run, not designed in |
| Player coverage (`common_player_info`) | — |
| Play-by-play only (hard tier) | `single-game-scoring-high-2015-16` |
| Refusal (metric not in the data) | `player-salary-refusal` |
