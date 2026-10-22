from datetime import UTC, date, datetime

from evals.comparator import (
    ERROR,
    EXACT,
    SUBSET,
    SUPERSET,
    TOLERANCE,
    UNORDERED,
    WRONG,
    Contract,
    compare,
)

CHI = [("CHI", 39), ("UTA", 38)]


def verdict(gold, agent, contract=None):
    return compare(gold, agent, contract).verdict


def test_identical_rows_are_exact():
    assert verdict(CHI, list(CHI)) == EXACT


def test_column_names_are_irrelevant():
    # The comparator never sees names; same values in the same shape is exact.
    assert verdict([("CHI", 39)], [("CHI", 39)]) == EXACT


def test_reordered_rows_pass_when_order_is_not_asked_for():
    assert verdict(CHI, list(reversed(CHI))) == EXACT


def test_reordered_rows_are_unordered_when_the_case_wants_order():
    assert verdict(CHI, list(reversed(CHI)), Contract(ordered=True)) == UNORDERED


def test_column_order_does_not_matter():
    assert verdict([("CHI", 39)], [(39, "CHI")]) == EXACT


def test_extra_column_is_still_a_match():
    result = compare([("CHI", 39)], [("CHI", 39, "1996-97")])
    assert result.verdict == EXACT
    assert "1 extra column" in result.detail


def test_extra_column_rejected_when_contract_forbids_it():
    assert verdict([("CHI", 39)], [("CHI", 39, "x")], Contract(allow_extra_columns=False)) == WRONG


def test_missing_column_is_wrong():
    assert verdict([("CHI", 39)], [("CHI",)]) == WRONG


def test_rounding_difference_is_tolerance_not_exact():
    assert verdict([(117.23,)], [(117.2300001,)]) == TOLERANCE


def test_real_difference_is_wrong():
    # The home-only Lakers PPG failure: plausible, and wrong.
    assert verdict([(117.2,)], [(117.0,)]) == WRONG


def test_numeric_string_matches_number():
    assert verdict([(30,)], [("30",)]) == EXACT


def test_numeric_string_coercion_can_be_turned_off():
    assert verdict([(30,)], [("30",)], Contract(coerce_numeric_strings=False)) == WRONG


def test_integer_and_float_are_equal():
    assert verdict([(39,)], [(39.0,)]) == EXACT


def test_whitespace_is_normalized():
    assert verdict([("Chicago Bulls",)], [("Chicago  Bulls ",)]) == EXACT


def test_timestamp_matches_date():
    assert verdict([(date(1996, 11, 1),)], [(datetime(1996, 11, 1, 0, 0, tzinfo=UTC),)]) == EXACT


def test_null_is_not_zero():
    assert verdict([(None,)], [(0,)]) == WRONG


def test_nulls_match_nulls():
    assert verdict([("CHI", None)], [("CHI", None)]) == EXACT


def test_extra_rows_are_superset():
    assert verdict(CHI, CHI + [("ATL", 36)]) == SUPERSET


def test_missing_rows_are_subset():
    assert verdict(CHI + [("ATL", 36)], CHI) == SUBSET


def test_empty_agent_result_is_subset():
    # The season_id = '1996-97' failure: no error, no rows.
    assert verdict(CHI, []) == SUBSET


def test_both_empty_is_exact():
    assert verdict([], []) == EXACT


def test_rows_where_gold_is_empty_are_superset():
    assert verdict([], CHI) == SUPERSET


def test_error_on_either_side():
    assert compare(CHI, [], agent_error="syntax error").verdict == ERROR
    assert compare([], CHI, gold_error="no such column").verdict == ERROR


def test_same_values_different_pairing_is_wrong():
    # Same column contents, rows scrambled across columns: a real failure.
    gold = [("CHI", 39), ("UTA", 38)]
    agent = [("CHI", 38), ("UTA", 39)]
    assert verdict(gold, agent) == WRONG


def test_duplicate_rows_are_not_collapsed():
    assert verdict([("CHI",), ("CHI",)], [("CHI",)]) == SUBSET
