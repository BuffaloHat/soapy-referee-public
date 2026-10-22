import pytest

from evals.comparator import EXACT, WRONG, Contract, compare
from evals.db import QueryResult, connect
from evals.resolve import Resolver, resolve_pair, season

CELTICS = "1610612738"
SPURS = "1610612759"
THUNDER = "1610612760"


@pytest.fixture(scope="module")
def resolver():
    con = connect()
    yield Resolver.from_snapshot(con)
    con.close()


@pytest.mark.parametrize(
    ("label", "team_id"),
    [
        ("Boston Celtics", CELTICS),
        ("BOS", CELTICS),
        ("  boston   celtics ", CELTICS),
        ("Celtics", CELTICS),
        (CELTICS, CELTICS),
        (int(CELTICS), CELTICS),
        ("SAN", SPURS),  # the Spurs' older abbreviation
        ("SAS", SPURS),
        ("Seattle SuperSonics", THUNDER),  # franchise survives relocation
        ("Oklahoma City Thunder", THUNDER),
    ],
)
def test_team_labels_resolve_to_team_id(resolver, label, team_id):
    assert resolver.team(label) == team_id


@pytest.mark.parametrize("label", ["Hornets", "Los Angeles", "Chicago", "not a team"])
def test_ambiguous_or_unknown_labels_pass_through(resolver, label):
    # "Hornets" names two franchises; a bare city is not a team.
    assert resolver.team(label) == label


def test_non_team_numbers_pass_through(resolver):
    assert resolver.team(39) == 39


@pytest.mark.parametrize(
    ("value", "start_year"),
    [("2007-08", 2007), ("2007-2008", 2007), ("1999-00", 1999), ("22007", 2007), ("2007", 2007), (2007, 2007)],
)
def test_season_forms_resolve_to_start_year(value, start_year):
    assert season(value) == start_year


@pytest.mark.parametrize("value", ["2007-8", "622007", "22007x", "last season", 22007])
def test_non_season_shapes_pass_through(value):
    # An integer 22007 could be an attendance figure; only strings are read as seasons.
    assert season(value) == value


def result(columns, rows):
    return QueryResult(columns=columns, rows=rows, truncated=False, elapsed_s=0.0)


def test_names_and_season_strings_match_gold_ids(resolver):
    contract = Contract(ordered=True, teams=("team_id",), seasons=("season_start",))
    gold = result(["team_id", "season_start", "improvement"], [(CELTICS, 2007, 0.512), (SPURS, 1997, 0.439)])
    agent = result(["team", "season", "change"], [("Boston Celtics", "2007-08", 0.512), ("SAS", "1997-98", 0.439)])

    gold_rows, agent_rows = resolve_pair(gold, agent, contract, resolver)
    assert compare(gold_rows, agent_rows, contract).verdict == EXACT


def test_wrong_team_is_still_wrong_after_resolution(resolver):
    contract = Contract(teams=("team_id",))
    gold = result(["team_id", "wins"], [(CELTICS, 66)])
    agent = result(["team", "wins"], [("San Antonio Spurs", 66)])

    gold_rows, agent_rows = resolve_pair(gold, agent, contract, resolver)
    assert compare(gold_rows, agent_rows, contract).verdict == WRONG


def test_nothing_resolves_unless_the_contract_asks(resolver):
    contract = Contract()
    gold = result(["team_id"], [(CELTICS,)])
    agent = result(["team"], [("Boston Celtics",)])

    gold_rows, agent_rows = resolve_pair(gold, agent, contract, resolver)
    assert compare(gold_rows, agent_rows, contract).verdict == WRONG


@pytest.mark.parametrize(
    ("name", "player_id"),
    [("Kobe Bryant", "977"), ("kobe  bryant", "977"), ("977", "977"), (977, "977"), ("Anthony Davis", "203076")],
)
def test_player_names_resolve_to_id(resolver, name, player_id):
    assert resolver.player(name) == player_id


def test_ambiguous_player_name_passes_through(resolver):
    # Three different players are named George Johnson.
    assert resolver.player("George Johnson") == "George Johnson"


def test_player_name_matches_gold_id(resolver):
    contract = Contract(players=("player_id",))
    gold = result(["player_id", "points"], [("977", 60)])
    agent = result(["player", "pts"], [("Kobe Bryant", 60)])

    gold_rows, agent_rows = resolve_pair(gold, agent, contract, resolver)
    assert compare(gold_rows, agent_rows, contract).verdict == EXACT
