"""Render the schema block the agent sees, in one of two modes.

`columns` is the control arm: CREATE TABLE-style DDL, names and types only —
the standard text-to-SQL baseline. `docs` is the treatment: the same tables
plus row counts, coverage, distinct counts, observed values, and any
hand-written descriptions.

Both modes render from `agent/schema_docs.yaml` and state facts only. Nothing
here may tell the agent how to query — no hints, no warnings, no worked
examples. That line is what makes a wrong answer the agent's own (see
overview.md, design principle 2).

Table order is catalog order unless a shuffle seed is given. Shuffling moves
whole tables and nothing else: columns keep their order, and no description
refers to another table by position, so every order states the same facts.
"""

import random
from functools import lru_cache
from pathlib import Path

import yaml

DOCS_PATH = Path(__file__).resolve().parent / "schema_docs.yaml"

MODES = ("columns", "docs")


class SchemaError(Exception):
    """The schema catalog is missing or malformed."""


@lru_cache(maxsize=1)
def _catalog(path: str | None = None) -> list[dict]:
    docs_path = Path(path) if path else DOCS_PATH
    if not docs_path.exists():
        raise SchemaError(f"no schema catalog at {docs_path} — run agent/build_schema_docs.py")

    raw = yaml.safe_load(docs_path.read_text()) or {}
    tables = raw.get("tables")
    if not tables:
        raise SchemaError(f"{docs_path.name}: no tables")
    return tables


def _render_columns(tables: list[dict]) -> str:
    """Names and types only. The control arm."""
    blocks = []
    for table in tables:
        lines = [f"CREATE TABLE {table['name']} ("]
        body = [f"  {c['name']} {c['type']}" for c in table["columns"]]
        lines.append(",\n".join(body))
        lines.append(");")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _literal(value) -> str:
    return f"'{value}'" if isinstance(value, str) else str(value)


def _format_values(column: dict) -> str:
    """Observed values, labelled for what they are.

    A complete set and a spread across a range are different claims, and an
    unlabelled list of three reads as "typical" whichever it is.
    """
    values = column.get("values") or []
    if column.get("values_complete"):
        return f"values {', '.join(_literal(v) for v in values)}" if values else ""

    parts = []
    if column.get("min") is not None and column.get("max") is not None:
        parts.append(f"range {_literal(column['min'])} to {_literal(column['max'])}")
    if values:
        parts.append(f"e.g. {', '.join(_literal(v) for v in values)}")
    return " · ".join(parts)


def _render_docs(tables: list[dict]) -> str:
    """Types plus the facts the catalog measured: coverage, spread, observed values."""
    blocks = []
    for table in tables:
        header = f"TABLE {table['name']} — {table['row_count']:,} rows"
        lines = [header]
        if table.get("description"):
            lines.append(f"  {table['description']}")

        width = max((len(c["name"]) for c in table["columns"]), default=0)
        for column in table["columns"]:
            # An empty table has no coverage to report; the 0-row header says it.
            facts = []
            if column["null_pct"] is not None:
                facts.append(f"populated {100 - column['null_pct']:.0f}%")
                facts.append(f"{column['distinct']:,} distinct")
            observed = _format_values(column)
            if observed:
                facts.append(observed)
            row = f"  {column['name']:<{width}}  {column['type']:<12}"
            lines.append(f"{row} {' · '.join(facts)}".rstrip())
            if column.get("description"):
                lines.append(f"  {'':<{width}}  {column['description']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _ordered(path: str | None, shuffle_seed: int | None) -> list[dict]:
    tables = list(_catalog(path))
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(tables)
    return tables


def build(mode: str, path: str | None = None, shuffle_seed: int | None = None) -> str:
    """The schema block for a run, selected by the config's `schema_mode`."""
    if mode not in MODES:
        raise SchemaError(f"unknown schema mode '{mode}' — expected one of {list(MODES)}")

    tables = _ordered(path, shuffle_seed)
    return _render_columns(tables) if mode == "columns" else _render_docs(tables)


def table_names(path: str | None = None, shuffle_seed: int | None = None) -> list[str]:
    """Table names in the order `build` renders them for the same seed."""
    return [t["name"] for t in _ordered(path, shuffle_seed)]
