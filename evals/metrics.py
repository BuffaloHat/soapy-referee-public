"""The eval's metrics, computed from a run's rows.

One definition per metric, in one place: docs/eval_plan.md §7 is the prose
version of this file. Every number in the eval report comes from here, so a
metric means the same thing wherever it is quoted.

Each row is one case attempt from the run record (`cases.jsonl`).
"""

from statistics import median

from evals import comparator
from evals.runner import PASSING, REFUSAL_ABSENT, REFUSAL_UNEXPECTED, stability

# Passing plus the near-misses a looser grader would accept.
LENIENT = PASSING | {comparator.UNORDERED, comparator.SUPERSET}

# Answered without error, and wrong: the failure that looks like success.
SILENT = frozenset({comparator.WRONG, comparator.SUBSET, comparator.SUPERSET,
                    comparator.UNORDERED, REFUSAL_ABSENT})

# Failures that announce themselves: SQL that did not run, or a refusal to
# answer something the data can answer.
LOUD = frozenset({comparator.ERROR, REFUSAL_UNEXPECTED})

PER_MILLION = 1_000_000


def count(rows: list[dict], verdicts) -> int:
    return sum(r["verdict"] in verdicts for r in rows)


def per_case(rows: list[dict]) -> dict[str, list[bool]]:
    """Pass or fail per attempt, by case, in attempt order."""
    cases: dict[str, list[bool]] = {}
    for row in sorted(rows, key=lambda r: r["attempt"]):
        cases.setdefault(row["case_id"], []).append(row["verdict"] in PASSING)
    return cases


def cost(rows: list[dict], price: dict | None) -> float | None:
    """Dollars for the run's model calls, cached input priced on its own.

    `price` is per million tokens: `input`, `cached_input`, `output`. None
    when the model has no price (a local model).
    """
    if not price:
        return None
    prompt = sum(r.get("prompt_tokens", 0) for r in rows)
    cached = sum(r.get("cached_tokens", 0) for r in rows)
    output = sum(r.get("output_tokens", 0) for r in rows)
    return ((prompt - cached) * price["input"] + cached * price["cached_input"]
            + output * price["output"]) / PER_MILLION


def summarize(rows: list[dict], price: dict | None = None) -> dict:
    """Every metric in eval_plan.md §7 for one run."""
    attempts = len(rows)
    passed = count(rows, PASSING)
    cases = per_case(rows)
    moved = stability([_Row(r) for r in rows])
    return {
        "attempts": attempts,
        "cases": len(cases),
        "pass": passed,
        "lenient": count(rows, LENIENT),
        "silent": count(rows, SILENT),
        "loud": count(rows, LOUD),
        "failures": attempts - passed,
        "pass_all": sum(all(p) for p in cases.values()),      # pass^k
        "pass_any": sum(any(p) for p in cases.values()),      # pass@k
        "verdict_changed": moved["verdict_changed"],
        "pass_changed": moved["pass_changed"],
        "median_model_s": median(r["model_elapsed_s"] for r in rows) if rows else 0.0,
        "cost": cost(rows, price),
    }


class _Row:
    """`runner.stability` reads attributes; the run record holds dicts."""

    def __init__(self, row: dict):
        self.case_id, self.attempt, self.verdict = row["case_id"], row["attempt"], row["verdict"]
