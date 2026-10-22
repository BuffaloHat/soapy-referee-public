"""Validate the comparator against the real snapshot.

A gold answer is only trustworthy if a second, independently written query
produces it too. These tests are the Phase 1 done-when criterion.
"""

import pytest

from evals.comparator import EXACT, Contract, compare
from evals.db import connect, run_sql

# "Which team had the best home record in 1996-97?" — calibrated against the
# published record: CHI 39-2.
BEST_HOME_RECORD_BY_FLAG = """
SELECT team_id_home, count(*) FILTER (WHERE wl_home = 'W') AS home_wins
FROM nba.game
WHERE season_id = '21996'
GROUP BY team_id_home
ORDER BY home_wins DESC
LIMIT 3
"""

# Same question, derived from the scores rather than the win/loss flag.
BEST_HOME_RECORD_BY_SCORE = """
WITH home_games AS (
    SELECT team_id_home AS team,
           CASE WHEN pts_home > pts_away THEN 1 ELSE 0 END AS won
    FROM nba.game
    WHERE season_id = '21996'
)
SELECT team, sum(won) AS wins
FROM home_games
GROUP BY team
ORDER BY wins DESC
LIMIT 3
"""

# A third route that looks equivalent and is not: line_score covers only
# 1,034 of the season's 1,189 games (trap 4).
BEST_HOME_RECORD_VIA_LINE_SCORE = """
SELECT g.team_id_home, count(*) FILTER (WHERE l.pts_home > l.pts_away) AS home_wins
FROM nba.game g
JOIN nba.line_score l ON l.game_id = g.game_id
WHERE g.season_id = '21996'
GROUP BY g.team_id_home
ORDER BY home_wins DESC
LIMIT 3
"""


@pytest.fixture(scope="module")
def con():
    connection = connect()
    yield connection
    connection.close()


def test_two_join_paths_agree(con):
    by_flag = run_sql(con, BEST_HOME_RECORD_BY_FLAG)
    by_score = run_sql(con, BEST_HOME_RECORD_BY_SCORE)
    result = compare(by_flag.rows, by_score.rows, Contract(ordered=True))
    assert result.verdict == EXACT, result.detail


def test_gold_matches_the_published_record(con):
    # CHI went 39-2 at home in 1996-97; team_id 1610612741 is Chicago.
    rows = run_sql(con, BEST_HOME_RECORD_BY_FLAG).rows
    assert rows[0] == ("1610612741", 39)


def test_line_score_path_is_not_equivalent(con):
    """The coverage gap changes the answer, with no error raised."""
    by_flag = run_sql(con, BEST_HOME_RECORD_BY_FLAG)
    via_line_score = run_sql(con, BEST_HOME_RECORD_VIA_LINE_SCORE)
    result = compare(by_flag.rows, via_line_score.rows, Contract(ordered=True))
    assert result.verdict != EXACT
