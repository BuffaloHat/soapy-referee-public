"""Compare two result sets and classify the difference into a verdict.

Correctness is a result-set question, never a SQL-text question. Any query
that returns the right rows is right, so everything here works on values:
column names are presentation and are never compared.

Verdicts, strictest first:

  exact      same rows, same order, values equal as returned
  tolerance  equal once numbers are rounded to the contract's decimals
  unordered  same rows, different order (only when the case wants an order)
  superset   agent returned the gold rows plus extra rows
  subset     agent returned some of the gold rows and nothing else
  wrong      neither set explains the other
  error      one side could not be produced

`superset` and `subset` are reported, never treated as correct — the gap
between strict and lenient accuracy is itself a finding.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from itertools import permutations

EXACT = "exact"
TOLERANCE = "tolerance"
UNORDERED = "unordered"
SUPERSET = "superset"
SUBSET = "subset"
WRONG = "wrong"
ERROR = "error"

# Above this, trying every column permutation is not worth it.
MAX_PERMUTED_COLUMNS = 7


@dataclass(frozen=True)
class Contract:
    """How one case wants its rows compared."""

    # Row order is part of the answer ("top 5", "earliest game").
    ordered: bool = False
    # Rounding applied for the `tolerance` verdict.
    decimals: int = 2
    # '30' and 30 mean the same thing in this snapshot's mixed-type columns.
    coerce_numeric_strings: bool = True
    # Extra agent columns are allowed; gold's columns must all be present.
    allow_extra_columns: bool = True
    # Gold columns holding teams and seasons, resolved before comparing
    # (see evals/resolve.py). The comparator itself never reads these.
    teams: tuple[str, ...] = ()
    seasons: tuple[str, ...] = ()
    players: tuple[str, ...] = ()


@dataclass(frozen=True)
class Comparison:
    verdict: str
    detail: str = ""
    matched_columns: tuple[int, ...] = field(default_factory=tuple)


def _canonical(value, contract: Contract, decimals: int | None):
    """One cell, reduced to something comparable across queries."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, str):
        text = " ".join(value.split())
        if contract.coerce_numeric_strings:
            try:
                value = float(text)
            except ValueError:
                return text
        else:
            return text
    if isinstance(value, (int, float)):
        number = float(value)
        return round(number, decimals) if decimals is not None else number
    if isinstance(value, datetime):
        # A date returned as a timestamp is the same answer.
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _column(rows, index, contract, decimals):
    return [_canonical(row[index], contract, decimals) for row in rows]


def _align_columns(gold_rows, agent_rows, gold_width, agent_width, contract, decimals):
    """Find which agent columns hold gold's columns, ignoring names and position.

    Returns the agent column indexes in gold's order, or None.
    """
    if agent_width == gold_width:
        straight = tuple(range(gold_width))
        if _columns_match(gold_rows, agent_rows, straight, contract, decimals):
            return straight
    elif not contract.allow_extra_columns or agent_width < gold_width:
        return None

    if agent_width > MAX_PERMUTED_COLUMNS:
        return None

    for candidate in permutations(range(agent_width), gold_width):
        if _columns_match(gold_rows, agent_rows, candidate, contract, decimals):
            return candidate
    return None


def _columns_match(gold_rows, agent_rows, candidate, contract, decimals):
    """Do these agent columns hold the same values as gold's, as multisets?"""
    for gold_index, agent_index in enumerate(candidate):
        gold_values = sorted(_column(gold_rows, gold_index, contract, decimals), key=_sort_key)
        agent_values = sorted(_column(agent_rows, agent_index, contract, decimals), key=_sort_key)
        if gold_values != agent_values:
            return False
    return True


def _sort_key(value):
    # NULLs and mixed types have to sort deterministically to be comparable.
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, float(value))
    if isinstance(value, (int, float)):
        return (1, float(value))
    return (2, str(value))


def _project(rows, columns):
    return [tuple(row[index] for index in columns) for row in rows]


def _rows(rows, contract, decimals):
    return [tuple(_canonical(cell, contract, decimals) for cell in row) for row in rows]


def compare(
    gold_rows,
    agent_rows,
    contract: Contract | None = None,
    gold_error: str | None = None,
    agent_error: str | None = None,
) -> Comparison:
    """Classify the agent's rows against the gold rows."""
    contract = contract or Contract()

    if gold_error or agent_error:
        side = "gold" if gold_error else "agent"
        return Comparison(ERROR, f"{side} query failed: {gold_error or agent_error}")

    gold_rows = list(gold_rows)
    agent_rows = list(agent_rows)

    if not gold_rows and not agent_rows:
        return Comparison(EXACT, "both empty")
    if not gold_rows:
        return Comparison(SUPERSET, f"gold empty, agent returned {len(agent_rows)} rows")
    if not agent_rows:
        return Comparison(SUBSET, "agent returned no rows")

    gold_width = len(gold_rows[0])
    agent_width = len(agent_rows[0])

    # Try without rounding first, so `exact` is not awarded to rounded matches.
    for decimals, verdict in ((None, EXACT), (contract.decimals, TOLERANCE)):
        columns = _align_columns(
            gold_rows, agent_rows, gold_width, agent_width, contract, decimals
        )
        if columns is None:
            continue

        projected = _project(agent_rows, columns)
        gold = _rows(gold_rows, contract, decimals)
        agent = _rows(projected, contract, decimals)

        if gold == agent:
            extra = f", {agent_width - gold_width} extra column(s)" if agent_width > gold_width else ""
            return Comparison(verdict, f"{len(gold)} rows{extra}", columns)
        if sorted(gold, key=_sort_key) == sorted(agent, key=_sort_key):
            if contract.ordered:
                return Comparison(UNORDERED, "same rows, different order", columns)
            return Comparison(verdict, f"{len(gold)} rows, order not compared", columns)

    # Column multisets do not line up: fall back to comparing whole rows.
    decimals = contract.decimals
    gold = _rows(gold_rows, contract, decimals)
    agent = _rows(agent_rows, contract, decimals)
    if gold_width == agent_width:
        # Result sets are bags: a duplicate row is a different answer.
        gold_bag, agent_bag = Counter(gold), Counter(agent)
        if gold_bag < agent_bag:
            extra = sum((agent_bag - gold_bag).values())
            return Comparison(SUPERSET, f"agent returned {extra} extra rows")
        if agent_bag < gold_bag:
            missing = sum((gold_bag - agent_bag).values())
            return Comparison(SUBSET, f"agent missing {missing} rows")

    return Comparison(
        WRONG,
        f"gold {len(gold)} rows × {gold_width} cols, agent {len(agent)} rows × {agent_width} cols",
    )
