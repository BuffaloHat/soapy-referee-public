"""Generate the column catalog in agent/schema_docs.yaml from the snapshot.

Stats and samples are regenerated on every run. Descriptions are hand-written
and preserved across runs.
"""

import datetime as dt
import decimal
import os
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("NBA_SQLITE_PATH", ROOT / "data" / "nba.sqlite"))
OUT_PATH = ROOT / "agent" / "schema_docs.yaml"

SAMPLE_ROWS = 10_000
SAMPLE_SEED = 42
MAX_SAMPLE_CHARS = 80

# At or below this many distinct values, list them all: the complete set is
# the encoding, and no sample beats it.
LOW_CARDINALITY = 12


def to_yaml_value(value):
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, str) and len(value) > MAX_SAMPLE_CHARS:
        return value[:MAX_SAMPLE_CHARS] + "..."
    return value


def load_existing_descriptions():
    if not OUT_PATH.exists():
        return {}
    docs = yaml.safe_load(OUT_PATH.read_text()) or {}
    found = {}
    for table in docs.get("tables", []):
        found[(table["name"], None)] = table.get("description", "")
        for column in table.get("columns", []):
            found[(table["name"], column["name"])] = column.get("description", "")
    return found


def column_values(con, table, quoted, distinct, low, high):
    """Observed values for one column: the whole set, or a spread across it.

    Sorting a sample and taking the first rows — the earlier approach — showed
    every column's low end only. For `season_id` that meant three preseason
    codes and no sign that regular-season games are encoded differently.
    """
    if distinct == 0:
        return {"values": [], "values_complete": True}

    if distinct <= LOW_CARDINALITY:
        # The full column, not the sample: `values_complete` has to be true.
        # A reservoir of 10,000 can miss a value that occurs a handful of times.
        rows = con.execute(
            f"SELECT DISTINCT {quoted} FROM nba.{table} WHERE {quoted} IS NOT NULL "
            f"ORDER BY {quoted}"
        ).fetchall()
        return {
            "values": [to_yaml_value(row[0]) for row in rows],
            "values_complete": True,
        }

    # The middle and one arbitrary value, from the reservoir sample. An exact
    # median over 13.6M play_by_play rows is the one aggregate worth avoiding.
    middle = con.execute(
        f"SELECT quantile_disc({quoted}, 0.5), any_value({quoted}) "
        f"FROM sample WHERE {quoted} IS NOT NULL"
    ).fetchone()

    picks = []
    for value in middle or ():
        if value is not None and value not in picks:
            picks.append(value)

    return {
        "min": to_yaml_value(low),
        "max": to_yaml_value(high),
        "values": [to_yaml_value(v) for v in picks],
        "values_complete": False,
    }


def profile_table(con, table, descriptions):
    columns = con.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_catalog = 'nba' AND table_name = ?
        ORDER BY ordinal_position
        """,
        [table],
    ).fetchall()
    quoted = [f'"{name}"' for name, _ in columns]

    # One scan for all column stats; the tables have no indexes. min and max
    # come from the whole column, so the reported range is the true one — a
    # range taken from a sample would be a narrower range stated as fact.
    aggregates = ", ".join(
        f"count({q}), count(DISTINCT {q}), min({q}), max({q})" for q in quoted
    )
    stats = con.execute(f"SELECT count(*), {aggregates} FROM nba.{table}").fetchone()
    row_count = stats[0]

    # Random sample rather than first rows, which skew to the earliest seasons.
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE sample AS "
        f"SELECT * FROM nba.{table} "
        f"USING SAMPLE reservoir({SAMPLE_ROWS} ROWS) REPEATABLE ({SAMPLE_SEED})"
    )

    column_docs = []
    for i, (name, data_type) in enumerate(columns):
        base = 1 + 4 * i
        non_null, distinct = stats[base], stats[base + 1]
        low, high = stats[base + 2], stats[base + 3]

        doc = {
            "name": name,
            "type": data_type,
            "description": descriptions.get((table, name), ""),
            "null_pct": round(100 * (row_count - non_null) / row_count, 1) if row_count else None,
            "distinct": distinct,
        }
        doc.update(column_values(con, table, quoted[i], distinct, low, high))
        column_docs.append(doc)

    return {
        "name": table,
        "description": descriptions.get((table, None), ""),
        "row_count": row_count,
        "columns": column_docs,
    }


def main():
    descriptions = load_existing_descriptions()
    con = duckdb.connect()
    con.execute(f"ATTACH '{DB_PATH}' AS nba (TYPE sqlite, READ_ONLY)")

    tables = [
        row[0]
        for row in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_catalog = 'nba' ORDER BY table_name"
        ).fetchall()
    ]

    docs = {"tables": []}
    for table in tables:
        print(f"profiling {table}...", flush=True)
        docs["tables"].append(profile_table(con, table, descriptions))

    header = (
        "# Agent schema docs — column catalog for the NBA snapshot.\n"
        "# Generated by agent/build_schema_docs.py. Stats and samples are overwritten\n"
        "# on each run; descriptions are preserved.\n"
        "# Descriptions state what a column contains, never how to query it.\n\n"
    )
    OUT_PATH.write_text(
        header + yaml.safe_dump(docs, sort_keys=False, allow_unicode=True, width=100)
    )
    print(f"wrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
