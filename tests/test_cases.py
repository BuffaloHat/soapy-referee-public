"""Every gold case must prove itself before it can judge anything.

For each answering case: the gold SQL runs, returns the row count the case
claims, and agrees with an independently written second query.
"""

import pytest
import yaml

from evals.cases import CASES_DIR, Case, CaseError, load_case, load_cases
from evals.comparator import EXACT, compare
from evals.db import connect, run_sql
from evals.resolve import Resolver, resolve_pair

CASES = load_cases()
ANSWERING = [case for case in CASES if case.answerable]


@pytest.fixture(scope="module")
def con():
    connection = connect()
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def resolver(con):
    return Resolver.from_snapshot(con)


def ids(cases):
    return [case.id for case in cases]


def test_cases_exist():
    assert CASES


@pytest.mark.parametrize("case", CASES, ids=ids(CASES))
def test_case_is_described(case: Case):
    assert case.question.endswith("?")
    assert case.provenance


@pytest.mark.parametrize("case", ANSWERING, ids=ids(ANSWERING))
def test_gold_sql_returns_the_expected_rows(con, case: Case):
    result = run_sql(con, case.gold_sql)
    assert len(result) == case.cardinality, result.rows
    assert not result.truncated


@pytest.mark.parametrize("case", ANSWERING, ids=ids(ANSWERING))
def test_gold_and_check_agree(con, resolver, case: Case):
    # check_sql goes through the same resolution an agent answer would.
    gold = run_sql(con, case.gold_sql)
    check = run_sql(con, case.check_sql)
    gold_rows, check_rows = resolve_pair(gold, check, case.contract, resolver)
    result = compare(gold_rows, check_rows, case.contract)
    assert result.verdict == EXACT, f"{result.verdict}: {result.detail}"


TOP_N = [
    case for case in ANSWERING
    if case.cardinality and f"LIMIT {case.cardinality}" in case.gold_sql
]


@pytest.mark.parametrize("case", TOP_N, ids=ids(TOP_N))
def test_no_tie_at_the_cutoff(con, case: Case):
    """If row N ties row N+1, LIMIT N picks one arbitrarily: gold is not deterministic."""
    n = case.cardinality
    widened = run_sql(con, case.gold_sql.replace(f"LIMIT {n}", f"LIMIT {n + 1}"))
    if len(widened) <= n:
        return
    identity = set(case.contract.teams) | set(case.contract.players) | set(case.contract.seasons)
    values = [i for i, name in enumerate(widened.columns) if name not in identity]
    last, next_row = widened.rows[n - 1], widened.rows[n]
    assert [last[i] for i in values] != [next_row[i] for i in values], (
        f"row {n} ties row {n + 1}: {last} vs {next_row}"
    )


@pytest.mark.parametrize("case", ANSWERING, ids=ids(ANSWERING))
def test_check_sql_is_a_different_query(case: Case):
    assert case.gold_sql.split() != case.check_sql.split()


def write_case(tmp_path, name, **overrides):
    raw = {
        "id": name,
        "question": "Which team had the best home record in the 1996-97 season?",
        "tier": "baseline",
        "expects": "rows",
        "provenance": "two queries agree",
        "cardinality": 1,
        "gold_sql": "SELECT 1",
        "check_sql": "SELECT 1 AS n",
    }
    raw.update(overrides)
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump({k: v for k, v in raw.items() if v is not None}))
    return path


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"tier": None}, "missing tier"),
        ({"tier": "medium"}, "tier must be"),
        ({"expects": "maybe"}, "expects must be"),
        ({"check_sql": None}, "needs check_sql"),
        ({"cardinality": None}, "needs cardinality"),
        ({"contract": {"ordered_by": "wins"}}, "unknown contract field"),
    ],
)
def test_malformed_case_is_rejected(tmp_path, overrides, message):
    path = write_case(tmp_path, "some-case", **overrides)
    with pytest.raises(CaseError, match=message):
        load_case(path)


def test_id_must_match_filename(tmp_path):
    path = write_case(tmp_path, "some-case", id="other-case")
    with pytest.raises(CaseError, match="does not match the filename"):
        load_case(path)


def test_refusal_case_must_not_carry_sql(tmp_path):
    path = write_case(tmp_path, "some-case", expects="refusal", tier="refusal")
    with pytest.raises(CaseError, match="must not carry gold_sql"):
        load_case(path)


def test_refusal_case_loads_without_sql(tmp_path):
    path = write_case(
        tmp_path,
        "no-such-metric",
        expects="refusal",
        tier="refusal",
        gold_sql=None,
        check_sql=None,
        cardinality=None,
    )
    case = load_case(path)
    assert not case.answerable


def test_cases_directory_is_the_default():
    assert CASES_DIR.name == "cases"
