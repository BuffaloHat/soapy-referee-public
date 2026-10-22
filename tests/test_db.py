import duckdb
import pytest

from evals.db import QueryError, connect, guard, run_sql


@pytest.fixture(scope="module")
def con():
    connection = connect()
    yield connection
    connection.close()


def test_select_returns_rows_and_columns(con):
    result = run_sql(con, "SELECT team_abbreviation_home AS team FROM nba.game LIMIT 3")
    assert result.columns == ["team"]
    assert len(result) == 3
    assert not result.truncated


def test_row_cap_truncates(con):
    result = run_sql(con, "SELECT game_id FROM nba.game", row_cap=10)
    assert len(result) == 10
    assert result.truncated


def test_timeout_raises(con):
    with pytest.raises(QueryError, match="timeout"):
        run_sql(con, "SELECT count(*) FROM nba.play_by_play a, nba.play_by_play b", timeout_s=0.5)


def test_connection_survives_timeout(con):
    with pytest.raises(QueryError):
        run_sql(con, "SELECT count(*) FROM nba.play_by_play a, nba.play_by_play b", timeout_s=0.5)
    assert run_sql(con, "SELECT 1 AS n").rows == [(1,)]


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t AS SELECT 1",
        "DROP TABLE nba.game",
        "UPDATE nba.game SET pts_home = 0",
        "ATTACH 'other.sqlite' AS other",
        "COPY (SELECT 1) TO 'out.csv'",
        "SET enable_external_access = true",
    ],
)
def test_non_select_rejected(sql):
    with pytest.raises(QueryError, match="only SELECT"):
        guard(sql)


def test_multiple_statements_rejected():
    with pytest.raises(QueryError, match="one statement"):
        guard("SELECT 1; DROP TABLE nba.game")


def test_invalid_sql_rejected():
    with pytest.raises(QueryError, match="could not parse"):
        guard("SELECT FROM WHERE")


def test_cte_allowed(con):
    result = run_sql(con, "WITH t AS (SELECT 1 AS n) SELECT n FROM t")
    assert result.rows == [(1,)]


def test_pragma_is_rewritten_to_read_only_select(con):
    # DuckDB turns PRAGMA into SELECT * FROM pragma_*, so the guard allows it.
    assert guard("PRAGMA database_list").startswith("SELECT")


def test_snapshot_is_read_only(con):
    with pytest.raises(duckdb.Error):
        con.execute("UPDATE nba.game SET pts_home = 0 WHERE game_id = '0021900001'")


def test_filesystem_access_blocked(con):
    with pytest.raises(duckdb.Error):
        con.execute("SELECT * FROM read_csv_auto('/etc/passwd')")


def test_leading_comment_allowed(con):
    result = run_sql(con, "-- calibration case\nSELECT 1 AS n")
    assert result.rows == [(1,)]
