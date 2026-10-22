"""The run record: what happened, written down as it happens.

This is the harness's own artifact, and the thing everything downstream reads.
Phoenix and Langfuse are viewers over it, not replacements for it — they are
local, tool-specific and ephemeral, and a verdict you cannot re-read in six
weeks is not evidence.

Shape is deliberately flat: one header, one row per case per attempt. No
nesting — that is the traces' job, and reinventing it here would produce a
worse version of it.

Each row does carry its trace's `trace_id` and root `span_id` (2026-10-05;
this file first said "no span ids"). Verdicts are sent to the tools as scores
after a run, and Phase 5 labels made in a tool come back keyed by those ids,
so the record needs them to join in either direction. They are join keys, not
a copy of the trace. Empty when the run was not traced.

Written incrementally. A run over the `docs` arm takes minutes, so a crash at
case eight should not cost cases one through seven, and a run in progress
should be readable while it runs.
"""

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_DIR = ROOT / "runs"

HEADER_FILE = "header.json"
DEFAULT_ENVIRONMENT = "dev"
CASES_FILE = "cases.jsonl"


@dataclass(frozen=True)
class CaseRow:
    """One case, one attempt. Everything needed to re-read the decision later."""

    case_id: str
    tier: str
    expects: str
    attempt: int
    verdict: str
    detail: str = ""

    question: str = ""
    sql: str | None = None
    refusal: str | None = None
    raw_response: str = ""

    prompt_tokens: int = 0
    output_tokens: int = 0
    # Part of output_tokens: hidden reasoning (OpenAI reasoning models only).
    reasoning_tokens: int = 0
    # Part of prompt_tokens: served from the provider's prompt cache (OpenAI).
    cached_tokens: int = 0
    done_reason: str = ""
    model_elapsed_s: float = 0.0

    gold_rows: int | None = None
    agent_rows: int | None = None
    # The first few rows of each answer, so a person can see "NULL vs 117.2"
    # without re-running anything. See `preview`.
    gold_preview: dict | None = None
    agent_preview: dict | None = None
    query_elapsed_s: float = 0.0

    error: str | None = None

    # Retries after a failed query (config `sql_retries`). `sql` is what was
    # finally scored; the first failed query and its error are kept here.
    retries: int = 0
    first_sql: str | None = None
    first_error: str | None = None

    # The prose answer (config `write_answer`), written from `agent_preview`
    # and nothing else, so a judge can check it against exactly what it saw.
    answer: str | None = None
    answer_elapsed_s: float = 0.0

    # The case attempt's trace: OTel hex ids, the same in every tool's API.
    trace_id: str | None = None
    span_id: str | None = None


PREVIEW_ROWS = 5
PREVIEW_CHARS = 60


def _plain(value):
    """A cell as JSON can hold it. Dates, decimals and the like become text."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def preview(result, limit: int = PREVIEW_ROWS) -> dict:
    """The head of a query result, for reading by eye: columns, the first
    rows, and how many there were in all."""
    return {
        "columns": list(result.columns),
        "rows": [[_plain(v) for v in row] for row in result.rows[:limit]],
        "total": len(result.rows),
        "truncated": result.truncated,
    }


def preview_text(head: dict) -> str:
    """A preview as a small text table — what the tools show as step output."""
    def cell(value) -> str:
        text = "NULL" if value is None else str(value)
        return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS - 1] + "…"

    total = head["total"]
    lines = [f"{total:,}{'+' if head['truncated'] else ''} row{'' if total == 1 else 's'}"]
    if head["columns"]:
        lines.append(" | ".join(head["columns"]))
    lines += [" | ".join(cell(v) for v in row) for row in head["rows"]]
    if total > len(head["rows"]):
        lines.append(f"… {total - len(head['rows']):,} more")
    return "\n".join(lines)


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def git_dirty() -> bool | None:
    """Whether the working tree differs from the commit. None outside git."""
    status = _git("status", "--porcelain")
    return None if status is None else bool(status.strip())


def git_commit() -> str | None:
    """Which code produced this run. Best effort — a run outside git is fine.

    A commit hash alone claims the run is reproducible from that commit. With
    uncommitted changes it is not, so the label says so: `4032c6d-dirty`.
    """
    commit = (_git("rev-parse", "--short", "HEAD") or "").strip()
    if not commit:
        return None
    return f"{commit}-dirty" if git_dirty() else commit


def environment() -> str:
    """Which kind of run this is: `dev` unless SREF_ENV says otherwise.

    Set `SREF_ENV=eval` for runs whose numbers count, so quick and broken runs
    can be filtered out of the tools' dashboards. Like an endpoint, it comes
    from the environment rather than the run config: it labels a run, it does
    not change an answer. Lowercase letters, digits, `-` and `_` only — the
    strictest of the tools' rules (Langfuse's).
    """
    value = os.environ.get("SREF_ENV", DEFAULT_ENVIRONMENT).strip() or DEFAULT_ENVIRONMENT
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", value) or value.startswith("langfuse"):
        raise ValueError(f"SREF_ENV={value!r}: use lowercase letters, digits, '-' or '_'")
    return value


@dataclass
class RunRecord:
    """Writes one run to disk as it goes."""

    run_id: str
    directory: Path
    header: dict = field(default_factory=dict)
    rows: list[CaseRow] = field(default_factory=list)

    @classmethod
    def start(cls, config, case_count: int, snapshot_sha: str,
              schema_block_chars: int, schema_orders: dict | None = None,
              runs_dir: Path | None = None) -> "RunRecord":
        stamp = datetime.now(UTC)
        run_id = f"{stamp.strftime('%Y%m%d-%H%M%S')}-{config.name}"
        directory = Path(runs_dir or DEFAULT_RUNS_DIR) / run_id
        directory.mkdir(parents=True, exist_ok=True)

        header = {
            "run_id": run_id,
            "started_at": stamp.isoformat(),
            "finished_at": None,
            "config": config.to_header(),
            # Gold answers are only valid against the file they were written
            # for, so the run has to say which file that was.
            "snapshot_sha256": snapshot_sha,
            "git_commit": git_commit(),
            "git_dirty": git_dirty(),
            "environment": environment(),
            "case_count": case_count,
            "schema_block_chars": schema_block_chars,
            # Table order per attempt, so a perturbed prompt can be rebuilt.
            "schema_orders": schema_orders,
        }
        record = cls(run_id=run_id, directory=directory, header=header)
        record._write_header()
        return record

    def _write_header(self) -> None:
        (self.directory / HEADER_FILE).write_text(json.dumps(self.header, indent=2) + "\n")

    def append(self, row: CaseRow) -> None:
        self.rows.append(row)
        with (self.directory / CASES_FILE).open("a") as handle:
            handle.write(json.dumps(asdict(row)) + "\n")

    def finish(self, stability: dict | None = None) -> None:
        self.header["finished_at"] = datetime.now(UTC).isoformat()
        self.header["verdicts"] = tally(self.rows)
        if stability is not None:
            self.header["stability"] = stability
        self._write_header()


def tally(rows: list[CaseRow]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def load(directory: Path) -> tuple[dict, list[dict]]:
    """Read a run back. Used by the reporting layer and the results viewer."""
    directory = Path(directory)
    header = json.loads((directory / HEADER_FILE).read_text())
    cases_path = directory / CASES_FILE
    rows = []
    if cases_path.exists():
        rows = [json.loads(line) for line in cases_path.read_text().splitlines() if line.strip()]
    return header, rows


def latest(runs_dir: Path | None = None) -> Path | None:
    directory = Path(runs_dir or DEFAULT_RUNS_DIR)
    if not directory.exists():
        return None
    runs = sorted(p for p in directory.iterdir() if (p / HEADER_FILE).exists())
    return runs[-1] if runs else None
