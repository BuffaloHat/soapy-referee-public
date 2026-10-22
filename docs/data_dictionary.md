# NBA Snapshot — Data Dictionary

**Source:** Kaggle `wyattowalsh/basketball`, file `nba.sqlite`
**Snapshot date:** published 2026-06-29; **data ends 2023-06-12**
**Size:** 2,349,588,480 bytes (2.2 GB)
**SHA-256:** `65b6412234d2d8817678d04f7cd2548aec82fec02b6c5b86679cade7572c21c7`
**Tables:** 16 · **Indexes:** none on any table
**Profiled:** 2026-09-15 · **Last updated:** 2026-10-20

> This snapshot is the ground truth for every gold case. It is frozen.
> If the checksum above changes, every gold answer is invalid.

## How to query it

No conversion needed — DuckDB reads the SQLite file in place:

```sql
INSTALL sqlite; LOAD sqlite;
ATTACH 'data/nba.sqlite' AS nba (TYPE sqlite, READ_ONLY);
SELECT * FROM nba.game LIMIT 5;
```

Materialize to native DuckDB only if a gold case proves too slow — the
13.6M-row `play_by_play` has no indexes and will scan.

---

## Table summary

| Table | Rows | Grain (one row per…) | Key | Unique? | Notes |
|---|---:|---|---|---|---|
| `game` | 65,698 | game, **wide home/away** | `game_id` | **No** — 56 dup (a) | Core fact. 1946–2023 |
| `play_by_play` | 13,592,899 | event within a game | `game_id` + `eventnum` | **No** — 7,360 exact copies (b) | **1996–2023 only** |
| `inactive_players` | 110,191 | player × game (inactive) | `game_id` + `player_id` | **No** — 7 exact copies (b) | |
| `officials` | 70,971 | official × game | `game_id` + `official_id` | **No** — 30 exact copies (b) | |
| `game_summary` | 58,110 | game × national broadcaster | `game_id` | **No** — 40 exact copies (b) + 49 broadcaster rows (c) | |
| `game_info` | 58,053 | game (attendance/time) | `game_id` | **No** — 40 exact copies (b) | 4 columns |
| `line_score` | 58,053 | game, **wide home/away** | `game_id` | **No** — 40 exact copies (b) | Per-quarter scoring |
| `other_stats` | 28,271 | game, **wide home/away** | `game_id` | **No** — 10 exact copies (b) | **1996+ only** |
| `draft_history` | 8,257 | player × draft | `person_id` + `season` | Yes (d) | 1947–2023 |
| `player` | 4,815 | player | `id` | Yes | Thin: id, name, is_active |
| `common_player_info` | 3,632 | player | `person_id` | Yes | Bio/physical, 33 cols |
| `draft_combine_stats` | 1,633 | player × combine year | `player_id` + `season` | Yes | 47 cols, mostly TEXT |
| `team` | 30 | **current** franchise | `id` | Yes | |
| `team_details` | 27 | franchise (arena/staff) | `team_id` | Yes | 3 teams missing |
| `team_history` | 50 | franchise era | `team_id` + `year_founded` | Yes | Relocations/renames |
| `team_info_common` | **0** | — | `team_id` + `season_year` (assumed) | Unverifiable | **EMPTY. 26 cols, no rows** |

No table declares a primary key or unique constraint — every key above is
inferred and verified by query (2026-09-17). No key column contains NULLs.

**Key notes**
- **(a)** `game`: all 56 duplicated `game_id`s are one row as `All Star` and
  one as `All-Star` (trap 2). `game_id` + `season_type` is unique, but only
  because of the spelling split.
- **(b)** Exact copies: whole rows repeated. Removing them makes the listed
  key unique — the grain holds, the rows are duplicated. `line_score` and
  `game_info` duplicate the same 40 games — all All-Star games, one per
  All-Star season. Joins and counts fan out on these.
- **(c)** `game_summary`: after removing exact copies, 45 games still have
  2–4 rows, one per national broadcaster (`TNT`, `ABC`, `ESPN`, and a junk
  value `test2`), 2016–2022. Only those columns differ. `game_id` +
  `natl_tv_broadcaster_abbreviation` is unique, but the broadcaster is NULL
  on 51,878 rows, so it isn't a clean key.
- **(d)** `draft_history`: `person_id` alone repeats for 139 players drafted
  in more than one year (e.g., Arvydas Sabonis, 1985 and 1986).
  `season` + `overall_pick` is not a key — 712 picks from 1947–1965 have
  `overall_pick = 0`.

---

## Core tables

### `game` — 65,698 rows, 55 columns
**Grain:** one row per game, with **both teams on the same row**.

Columns come in `_home`/`_away` pairs: `team_id_*`, `team_abbreviation_*`,
`team_name_*`, `matchup_*`, `wl_*`, and the box score
(`fgm/fga/fg_pct/fg3m/fg3a/fg3_pct/ftm/fta/ft_pct/oreb/dreb/reb/ast/stl/blk/tov/pf/pts/plus_minus`).
Plus `game_id`, `game_date`, `season_id`, `season_type`, and a single
un-suffixed `min`.

**This is the most important table and the richest trap source.** See below.

**`team_id` identifies a franchise, not a city.** It survives relocations
and renames: Seattle and Oklahoma City share `1610612760`. Group by
`team_id` for franchise history; grouping by abbreviation splits a
franchise in two.

### `play_by_play` — 13,592,899 rows, 34 columns
**Grain:** one event within one game (`game_id` + `eventnum`).

`eventmsgtype` / `eventmsgactiontype` encode the event kind numerically
(no lookup table ships with the snapshot). Three participant slots
(`player1_*`, `player2_*`, `player3_*`) with id/name/team — role depends
on event type. Text lives in `homedescription`, `visitordescription`,
`neutraldescription`. Running `score` and `scoremargin` are **TEXT**.

**The running `score` is not reliable for deriving points** (verified
2026-09-21, 2015-16). Period start/end events (types 12, 13) carry a score,
and some games have scores out of order or mistyped — one game steps from
`47 - 55` to `45 - 55`. Score deltas credit impossible totals (77 points to
one player). Scoring descriptions are reliable: each carries the player's
running total, e.g. `(60 PTS)`.

### `line_score` — 58,053 rows, 43 columns
**Grain:** one row per game, wide home/away. Quarter scoring
`pts_qtr1..4_*` plus `pts_ot1..10_*`.

### `player` / `common_player_info`
`player` is a thin roster list (4,815 rows; **only 535 marked active**).
`common_player_info` has bio detail for 3,632 of them.

---

## Trap surface

These are verified, not hypothesized. Most are gold-case seeds.

### 1. Wide home/away denormalization — *the headline trap*
A team's games are split across `team_id_home` and `team_id_away`.

> "What did the Lakers average in 2022-23?"

Filtering `team_id_home = LAL` returns **only home games** and a
plausible, wrong number. Correct answers need `UNION ALL` or `CASE`.
Same applies to `line_score` and `other_stats`.

### 2. `All Star` vs `All-Star` — dirty categorical
`game.season_type` has **both spellings for the same games**:

```
Regular Season  60,192      All Star   65
Playoffs         3,842      All-Star   63
Pre Season       1,536
```

Game `0030100001` appears twice — once each spelling, identical date and
score. **This is also why `game_id` is not unique** (65,698 rows,
65,642 distinct). Any join on `game_id` fans out on these.

### 3. Era-dependent NULLs — *silent wrong aggregates*
Stats were not tracked in early eras, and NULLs are not zeros:

| Column | NULL rate |
|---|---:|
| `tov_home` | 28.4% |
| `reb_home` | 23.9% |
| `fg3m_home` | 20.1% (3-pt line arrived 1979) |
| `pts_home` | 0.0% |

> "Who has the most career rebounds?"

silently computes over 76% of games. `AVG()` skips NULLs; `SUM()` treats
them as absent. Both look fine.

**Where the NULLs are** (regular season, verified 2026-09-21): not only the
earliest eras. `reb_*` is NULL for nearly every game through 1980-81 (78%
in 1980-81), then 1–2% from 1981-82 on. `tov_*` is ~92% NULL through
1984-85. `fg3m_*` is complete from 1979-80. So game-level rebounds exist
only from the early 1980s, even though the league tracked them from 1950.

**The SQL-level trap is averaging.** In a season with a few NULL games,
`sum(reb) / count(*)` counts unrecorded games as zero and returns a
plausible, slightly low average; `avg(reb)` does not. In 1982-83 that
swaps the #2 and #3 rebounding teams. A career *total* does not
discriminate this way — any `SUM()` covers the same recorded games — so its
failure is in the prose: not saying the total starts in the 1980s.

### 4. Coverage gaps between tables
Joining `game` to anything else drops rows silently:

| Join target | `game` rows missing from it |
|---|---:|
| `other_stats` | 37,381 (57%) |
| `play_by_play` | 35,824 (55%) — PBP starts 1996 |
| `game_info` | 7,629 |
| `line_score` | 7,629 |

An inner join to `play_by_play` answers a "career" question using half
the league's history.

The same gap exists on the player side. `common_player_info` (3,632 rows)
does not cover the players named in the other tables:

| Source | Players missing from `common_player_info` |
|---|---:|
| `player` (4,815) | 1,183 (25%) — 115 of them flagged active |
| `draft_history` (8,118 people) | 5,482 (68%) |
| `play_by_play` (`player1_id`) | 1,911 |

Verified 2026-09-17. Both 2015 and 2023 No. 1 overall picks
(Karl-Anthony Towns, Victor Wembanyama) are absent, so an inner join from
`draft_history` to `common_player_info` returns a shorter list that still
looks complete.

### 5. Type mismatch across join keys
`team_id` is **TEXT** everywhere except `common_player_info.team_id`,
which is **INTEGER**. In SQLite a strict equality join returns zero rows.

**Through DuckDB — the harness's engine — it is not silent** (verified
2026-09-21). `c.team_id = t.id` casts implicitly and returns the correct
rows. `team_id IN (SELECT id ...)` raises a binder error ("an explicit cast
is required"). Either way there is no silent wrong answer, so this trap
yields an `error` verdict at most, not a gold case.

Related: `common_player_info.team_id` is `0` for 597 players, and for
inactive players it holds their last team, not a current roster.

### 6. Mixed storage types within one table
`line_score` quarter columns are inconsistently typed *per column*:

```
pts_qtr1_home  TEXT      pts_qtr1_away  INTEGER
pts_qtr2_away  TEXT      pts_qtr4_away  INTEGER
```

Summing quarters needs casting. Worse, TEXT comparison sorts `'9' > '10'`.

**Through DuckDB** (verified 2026-09-21): `UNION ALL` of TEXT and INTEGER
quarter columns yields TEXT, silently. `greatest()` across them raises an
error. On the resulting text:
- **`MIN()` is silently wrong.** `'10'` sorts before `'7'`, so the fewest
  points in a quarter comes back as 10 in every season checked (1996–2022),
  whatever the true minimum.
- **`MAX()` hides the trap in modern seasons.** When every value has two
  digits, text and numeric order agree (2022-23: both 55). Across all
  seasons, text `MAX()` is `'9'` against a true 55.

### 7. Dimension tables that don't cover the facts
`team` has **30 rows** (current franchises) but `game` references
**63 distinct** `team_id_home` values — defunct and relocated franchises.
An inner join to `team` silently drops 33 franchises. `team_history` (50
rows) holds the era detail; `team_details` is missing 3 current teams.

### 8. The empty table
`team_info_common` has 26 columns and **zero rows**. It looks queryable in
schema context and returns nothing. A plausible agent target for standings
questions.

### 9. Four overlapping game tables
`game`, `game_info`, `game_summary`, `line_score` all key on `game_id`
with different grain, coverage, and column sets. Table selection is
genuinely ambiguous.

### 10. Missing regular seasons — *silent gaps in "previous season"*
Seven regular seasons are absent from `game`, though their All-Star and
playoff games are present (and 2012-13 keeps its preseason):

```
1960-61  1961-62  1966-67  1970-71  1975-76  1976-77  2012-13
```

Any "previous season" or "consecutive seasons" logic built on row order —
`LAG()` / `LEAD()` over a team's seasons — silently treats 2011-12 and
2013-14 as back to back. Year-over-year changes, streaks, and "the season
after" all cross the gap without an error. Questions about these seasons'
regular season return no rows. Verified 2026-09-21.

Short seasons are real, not gaps: 1998-99 had 50 games (lockout), 2011-12
had 66 (lockout), 2019-20 had 64–75 (COVID), 2020-21 had 72, and teams
folded mid-season in 1949-50 and 1950-51. Win totals across those seasons
are not comparable; win percentage is.

### 11. `game` and `line_score` disagree on home and away — *cross-table*
Joined on `game_id`, the two tables name a different home team on 27,237 of
58,149 rows; in the 2018-19 regular season, 541 of 1,125 (verified
2026-10-20). A join on `game_id` plus both team ids — a careful-looking join
— silently drops those games, so a per-team or per-quarter answer misses
them without an error.

Single-table profiling missed this; a model run found it (findings article
12). Profile tables against each other, not only one at a time.

---

## Not in this snapshot — read this before writing gold cases

**There are no player-level box scores.** No table holds a player's points,
rebounds or assists for a game. `game` is
team-level; `player` and `common_player_info` are biographical.

Player stats are *derivable* from `play_by_play`, but only by parsing the
description text. Verified event coding:

```
eventmsgtype 1 = made field goal     "O'Neal  Slam Dunk (2 PTS) (Van Exel 1 AST)"
eventmsgtype 2 = missed field goal   "MISS Ceballos 26' 3PT Jump Shot"
eventmsgtype 3 = free throw          "Campbell Free Throw 1 of 2 (1 PTS)"
                                     "MISS Campbell Free Throw 2 of 2"
```

There is no code lookup table, and no numeric points column. To total a
player's points: filter `eventmsgtype = 1`, detect `'3PT'` in the
description to score 2 vs 3, then add `eventmsgtype = 3` rows that do *not*
start with `MISS`. The parenthetical `(N PTS)` is a **running game total**,
not the value of that event — using it as a per-event value is itself a
trap.

**Consequence for the golden set.** Question types split into two tiers:

- **Team and game level** — well supported, clean, and carrying the
  traps above. This is where the bulk of the gold set should live.
- **Player level** — requires play-by-play string parsing over 13.6M rows,
  1996+ only. Genuinely hard, legitimately interesting as a "hard tier,"
  but a small number of cases, not the backbone.

Questions like "who led the league in scoring in 2022-23" are answerable
but expensive and error-prone. Treat them as the difficult tier, not the
warm-up.

Also absent entirely: `ts_pct`, `efg_pct`, `usg_pct`, `pie`, off/def/net
rating, pace, possessions, four factors, lineup and tracking data. No gold
case can be checked against a published advanced metric.
