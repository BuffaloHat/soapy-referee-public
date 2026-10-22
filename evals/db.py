"""The one path to the snapshot: gold SQL and agent SQL both run through here.

Read-only, one statement, with a timeout and a row cap, so an agent query can
never modify, read the filesystem, or hang a run.

Read-only introspection (PRAGMA, duckdb_* functions) stays available: DuckDB
rewrites it into SELECT, and it exposes metadata, not eval internals.
"""

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from evals.snapshot import check_file

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(os.environ.get("NBA_SQLITE_PATH", ROOT / "data" / "nba.sqlite"))
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_ROW_CAP = 10_000


class QueryError(Exception):
    """The query was rejected, timed out, or failed to execute."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_s: float

    def __len__(self) -> int:
        return len(self.rows)


def connect(db_path: Path | None = None, verify: bool = True) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB with the snapshot attached read-only as `nba`."""
    db_path = Path(db_path or DEFAULT_DB_PATH)
    if verify:
        check_file(db_path)

    con = duckdb.connect()
    con.execute(f"ATTACH '{db_path}' AS nba (TYPE sqlite, READ_ONLY)")
    con.execute("USE nba")
    # Attach first: from here on, queries cannot reach any other file.
    con.execute("SET enable_external_access = false")
    return con


def guard(sql: str) -> str:
    """Return the single SELECT in `sql`, or raise. Parsed, not pattern-matched."""
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise QueryError(f"could not parse SQL: {exc}") from exc

    if not statements:
        raise QueryError("no SQL statement found")
    if len(statements) > 1:
        raise QueryError(f"expected one statement, found {len(statements)}")

    statement = statements[0]
    if statement.type != duckdb.StatementType.SELECT:
        raise QueryError(f"only SELECT is allowed, found {statement.type.name}")
    return statement.query


def run_sql(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    row_cap: int = DEFAULT_ROW_CAP,
) -> QueryResult:
    """Execute one read-only statement under a timeout and a row cap."""
    statement = guard(sql)

    # DuckDB has no query timeout; interrupt the connection from a timer instead.
    timed_out = threading.Event()

    def interrupt():
        timed_out.set()
        con.interrupt()

    timer = threading.Timer(timeout_s, interrupt)
    timer.start()
    started = time.perf_counter()
    try:
        cursor = con.execute(statement)
        # One row past the cap tells us the result was truncated.
        rows = cursor.fetchmany(row_cap + 1)
        columns = [description[0] for description in cursor.description]
    except duckdb.Error as exc:
        if timed_out.is_set():
            raise QueryError(f"query exceeded {timeout_s:g}s timeout") from exc
        raise QueryError(str(exc)) from exc
    finally:
        timer.cancel()
        elapsed = time.perf_counter() - started

    truncated = len(rows) > row_cap
    return QueryResult(
        columns=columns,
        rows=rows[:row_cap],
        truncated=truncated,
        elapsed_s=elapsed,
    )
