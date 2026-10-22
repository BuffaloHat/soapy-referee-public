"""The eval's metrics mean what eval_plan.md §7 says they mean."""

import pytest

from evals import metrics


def rows(verdicts: dict[str, list[str]], **extra) -> list[dict]:
    return [{"case_id": c, "attempt": i + 1, "verdict": v, "model_elapsed_s": 1.0, **extra}
            for c, vs in verdicts.items() for i, v in enumerate(vs)]


RUN = rows({"a": ["exact"] * 3, "b": ["exact", "wrong", "tolerance"],
            "c": ["error", "subset", "wrong"], "r": ["refusal_correct"] * 3})


def test_pass_counts_exact_tolerance_and_a_correct_refusal():
    assert metrics.summarize(RUN)["pass"] == 8


def test_silent_failures_are_answers_that_ran_and_failed():
    # wrong, subset, wrong — the error is loud, not silent.
    s = metrics.summarize(RUN)
    assert (s["silent"], s["loud"], s["failures"]) == (3, 1, 4)


def test_answering_a_question_that_should_be_declined_is_silent():
    assert metrics.summarize(rows({"r": ["refusal_absent"]}))["silent"] == 1


def test_declining_an_answerable_question_is_loud():
    assert metrics.summarize(rows({"a": ["refusal_unexpected"]}))["loud"] == 1


def test_lenient_adds_unordered_and_superset_only():
    s = metrics.summarize(rows({"a": ["unordered", "superset", "subset"]}))
    assert (s["pass"], s["lenient"]) == (0, 2)


def test_pass_all_and_pass_any_are_per_case():
    s = metrics.summarize(RUN)
    assert (s["pass_all"], s["pass_any"], s["cases"]) == (2, 3, 4)


def test_stability_counts_moved_verdicts_and_pass_line_crossings():
    s = metrics.summarize(RUN)
    assert (s["verdict_changed"], s["pass_changed"]) == (2, 1)


def test_cached_input_is_priced_at_its_own_rate():
    run = rows({"a": ["exact"]}, prompt_tokens=1_000_000, cached_tokens=600_000,
               output_tokens=100_000)
    price = {"input": 2.0, "cached_input": 0.1, "output": 10.0}
    # 400k uncached at $2 + 600k cached at $0.10 + 100k output at $10
    assert metrics.cost(run, price) == pytest.approx(0.8 + 0.06 + 1.0)


def test_a_local_model_has_no_cost():
    assert metrics.summarize(RUN)["cost"] is None
