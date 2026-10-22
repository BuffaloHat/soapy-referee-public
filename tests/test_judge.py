"""The judge reads its replies safely, sees only what it should, and is scored
against people the right way round.

No model is called: replies are given as text.
"""

import pytest

from evals import cases, judge


@pytest.fixture
def cfg():
    return judge.load_config("faithfulness-gemma")


@pytest.mark.parametrize("text, faithful", [
    ("FAITHFUL\nThe number matches.", True),
    ("UNFAITHFUL\nAdds a claim.", False),
    ("**UNFAITHFUL**\nAdds a claim.", False),     # markdown around the verdict
    ("\n\nFaithful\nok", True),
    ("Line 1: UNFAITHFUL\nLine 2: 259.8, not 260.0.", False),   # echoes the prompt
    ("I think it is fine.", None),                 # no verdict: unreadable, not a pass
    ("", None),
])
def test_the_first_line_decides(text, faithful):
    verdict = judge.parse(text)
    assert verdict.faithful is faithful
    assert verdict.raw == text


def test_the_reason_drops_an_echoed_label():
    assert judge.parse("Line 1: FAITHFUL\nLine 2: matches.").reason == "matches."


def test_unfaithful_is_not_read_as_faithful():
    # "UNFAITHFUL" contains "FAITHFUL"; the order of the checks matters.
    assert judge.parse("UNFAITHFUL").faithful is False


def test_the_judge_sees_question_rows_and_answer_only(cfg):
    system, user = judge.build(cfg, "How many?", "1 row\ngames\n65", "There are 65.")
    assert user == "Question: How many?\n\nQuery result:\n1 row\ngames\n65\n\nAnswer: There are 65."
    assert "FAITHFUL or UNFAITHFUL" in system


def test_the_judge_is_never_shown_gold(cfg):
    # It grades faithfulness, not correctness (overview.md, principle 6).
    system, _ = judge.build(cfg, "", "", "")
    for case in cases.load_cases():
        if case.gold_sql:
            assert case.gold_sql.strip()[:40].lower() not in system.lower()


def test_agreement_counts_which_way_the_judge_errs():
    # (human faithful?, judge faithful?)
    pairs = [(True, True), (False, False), (False, True), (True, False), (True, None)]
    assert judge.agreement(pairs) == {
        "items": 5, "agree": 2, "lenient": 1, "strict": 1, "unreadable": 1}
