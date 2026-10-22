"""Load and validate gold cases.

A case is the question, the answer as a program, and the terms of comparison.
Nothing here stores an expected result: `gold_sql` runs at eval time against
the same snapshot the agent queried.

Every answering case carries a `check_sql` — the same answer by a different
route. A gold answer nobody cross-checked is a guess, and a wrong gold answer
is worse than no case at all.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from evals.comparator import Contract

CASES_DIR = Path(__file__).resolve().parent / "cases"

TIERS = {"baseline", "trap", "hard", "refusal"}
EXPECTS = {"rows", "refusal"}
CONTRACT_FIELDS = {
    "ordered", "decimals", "coerce_numeric_strings", "allow_extra_columns",
    "teams", "seasons", "players",
}
REQUIRED = {"id", "question", "tier", "expects", "provenance"}


class CaseError(Exception):
    """A case is malformed. Loud at load time, not silent at run time."""


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    tier: str
    expects: str
    provenance: str
    gold_sql: str | None = None
    check_sql: str | None = None
    cardinality: int | None = None
    traps: list[str] = field(default_factory=list)
    contract: Contract = field(default_factory=Contract)
    notes: str = ""

    @property
    def answerable(self) -> bool:
        return self.expects == "rows"


def _validate(raw: dict, path: Path) -> None:
    missing = REQUIRED - raw.keys()
    if missing:
        raise CaseError(f"{path.name}: missing {', '.join(sorted(missing))}")
    if raw["id"] != path.stem:
        raise CaseError(f"{path.name}: id '{raw['id']}' does not match the filename")
    if raw["tier"] not in TIERS:
        raise CaseError(f"{path.name}: tier must be one of {sorted(TIERS)}")
    if raw["expects"] not in EXPECTS:
        raise CaseError(f"{path.name}: expects must be one of {sorted(EXPECTS)}")

    unknown = set(raw.get("contract") or {}) - CONTRACT_FIELDS
    if unknown:
        raise CaseError(f"{path.name}: unknown contract field(s) {', '.join(sorted(unknown))}")

    if raw["expects"] == "rows":
        for required in ("gold_sql", "check_sql"):
            if not (raw.get(required) or "").strip():
                raise CaseError(f"{path.name}: an answering case needs {required}")
        if raw.get("cardinality") is None:
            raise CaseError(f"{path.name}: an answering case needs cardinality")
    else:
        for forbidden in ("gold_sql", "check_sql"):
            if raw.get(forbidden):
                raise CaseError(f"{path.name}: a refusal case must not carry {forbidden}")


def _contract(raw: dict) -> Contract:
    fields = dict(raw)
    for name in ("teams", "seasons", "players"):
        if name in fields:
            fields[name] = tuple(fields[name])
    return Contract(**fields)


def load_case(path: Path) -> Case:
    raw = yaml.safe_load(path.read_text()) or {}
    _validate(raw, path)
    return Case(
        id=raw["id"],
        question=raw["question"].strip(),
        tier=raw["tier"],
        expects=raw["expects"],
        provenance=raw["provenance"].strip(),
        gold_sql=(raw.get("gold_sql") or "").strip() or None,
        check_sql=(raw.get("check_sql") or "").strip() or None,
        cardinality=raw.get("cardinality"),
        traps=list(raw.get("traps") or []),
        contract=_contract(raw.get("contract") or {}),
        notes=(raw.get("notes") or "").strip(),
    )


def load_cases(directory: Path | None = None) -> list[Case]:
    directory = directory or CASES_DIR
    cases = [load_case(path) for path in sorted(directory.glob("*.yaml"))]
    ids = [case.id for case in cases]
    duplicates = {case_id for case_id in ids if ids.count(case_id) > 1}
    if duplicates:
        raise CaseError(f"duplicate case ids: {', '.join(sorted(duplicates))}")
    return cases
