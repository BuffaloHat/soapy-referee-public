"""Verdicts sent to the tools as their native grades.

Trace status says whether code threw, not whether the answer was right
(docs/observability_tools.md §6, lesson 8). So each case attempt's verdict is
sent to every tool as that tool's own kind of grade — smith feedback, fuse
score, phx annotation — on the root of the attempt's trace.

Two grades per attempt, the same in every tool:

    verdict   categorical   exact, wrong, error, refusal_correct, ...
    pass      boolean       the runner's PASSING line

Sent from the run record after the run, not from inside it. The record is the
source of truth, so grades can be re-sent from it at any time; and a grade sent
before its trace has landed can be rejected. Re-sending is safe: each grade has
an id built from its span and name, so every tool updates it in place.

Which tools receive grades follows the same `.env` settings as tracing. smith
feedback is sent without `extend_trace_retention`, so graded traces stay on the
free 14-day tier.

    uv run python -m evals.scores                  # the latest run
    uv run python -m evals.scores --run <run_id>
"""

import argparse
import base64
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from evals import record
from evals.api import http
from evals.runner import PASSING

# Fixed, so a grade's id is the same every time it is sent.
GRADE_NAMESPACE = uuid.UUID("5f1c9e2a-7d3b-4c1e-9a6f-2b8d0e4c7a31")


@dataclass(frozen=True)
class Grade:
    name: str
    label: str
    score: float | None = None     # None for a categorical grade
    comment: str = ""


def grades(row: dict) -> list[Grade]:
    passed = row["verdict"] in PASSING
    return [
        Grade("verdict", row["verdict"], comment=row.get("detail") or ""),
        Grade("pass", "pass" if passed else "fail", score=1.0 if passed else 0.0),
    ]


def smith_run_id(span_id: str) -> str:
    """smith's id for a span: the OTel span id, zero-padded into a UUID.

    Checked against smith's API: span `15d103ff9f2dfc5d` is run
    `00000000-0000-0000-15d1-03ff9f2dfc5d`. A root run's id is also its trace id.
    """
    return str(uuid.UUID(int=int(span_id, 16)))


# --- payloads, one builder per tool ----------------------------------------

def smith_feedback(row: dict, grade: Grade) -> dict:
    run_id = smith_run_id(row["span_id"])
    body = {
        "id": str(uuid.uuid5(GRADE_NAMESPACE, f"{row['span_id']}:{grade.name}")),
        "run_id": run_id,
        "trace_id": run_id,
        "key": grade.name,
        "comment": grade.comment or None,
        "feedback_source": {"type": "api"},
    }
    if grade.score is None:
        body["value"] = grade.label
    else:
        body["score"] = grade.score
    return body


def fuse_score(row: dict, grade: Grade, environment: str) -> dict:
    return {
        "id": f"{row['trace_id']}-{grade.name}",
        "traceId": row["trace_id"],
        # On the case's root step, not the trace: where hand labels sit, and
        # where fuse reads an experiment item's scores.
        "observationId": row["span_id"],
        "name": grade.name,
        "dataType": "CATEGORICAL" if grade.score is None else "BOOLEAN",
        "value": grade.label if grade.score is None else grade.score,
        "comment": grade.comment or None,
        # Scores carry their own environment; without it they sit in
        # `default` while their traces sit in `dev` or `eval`.
        "environment": environment,
    }


def phx_annotation(row: dict, grade: Grade) -> dict:
    # phx upserts on (name, span_id), so a re-send replaces the annotation.
    result = {"label": grade.label}
    if grade.score is not None:
        result["score"] = grade.score
    if grade.comment:
        result["explanation"] = grade.comment
    return {"span_id": row["span_id"], "name": grade.name,
            "annotator_kind": "CODE", "result": result}


# --- sending ---------------------------------------------------------------

def _ok(status: int) -> bool:
    return 200 <= status < 300


def send_smith(rows, environment, call=http) -> tuple[int, list[str]]:
    base = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com").rstrip("/")
    headers = {"x-api-key": os.environ["LANGSMITH_API_KEY"]}
    sent, errors = 0, []
    for row in rows:
        for grade in grades(row):
            body = smith_feedback(row, grade)
            status, text = call("POST", f"{base}/api/v1/feedback", headers, body)
            if status == 409:  # already sent once: update it instead
                update = {k: v for k, v in body.items() if k in ("score", "value", "comment")}
                status, text = call("PATCH", f"{base}/api/v1/feedback/{body['id']}",
                                    headers, update)
            if _ok(status):
                sent += 1
            else:
                errors.append(f"{status} {text}")
    return sent, errors


def send_fuse(rows, environment, call=http) -> tuple[int, list[str]]:
    host = os.environ["LANGFUSE_HOST"].rstrip("/")
    keys = f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}"
    headers = {"Authorization": f"Basic {base64.b64encode(keys.encode()).decode()}"}
    sent, errors = 0, []
    for row in rows:
        for grade in grades(row):
            status, text = call("POST", f"{host}/api/public/scores", headers,
                                fuse_score(row, grade, environment))
            if _ok(status):
                sent += 1
            else:
                errors.append(f"{status} {text}")
    return sent, errors


def send_phx(rows, environment, call=http) -> tuple[int, list[str]]:
    endpoint = os.environ["PHOENIX_COLLECTOR_ENDPOINT"].rstrip("/")
    data = [phx_annotation(row, grade) for row in rows for grade in grades(row)]
    status, text = call("POST", f"{endpoint}/v1/span_annotations?sync=true", {}, {"data": data})
    return (len(data), []) if _ok(status) else (0, [f"{status} {text}"])


def senders_from_env() -> dict:
    """The tools to grade in: those whose settings are in the environment."""
    senders = {}
    if os.environ.get("PHOENIX_COLLECTOR_ENDPOINT"):
        senders["phoenix"] = send_phx
    if all(os.environ.get(k) for k in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY",
                                       "LANGFUSE_SECRET_KEY")):
        senders["langfuse"] = send_fuse
    if os.environ.get("LANGSMITH_API_KEY"):
        senders["langsmith"] = send_smith
    return senders


def send_run(directory: Path, senders: dict, call=http) -> dict:
    """Grade every traced row of one run in each tool. One tool failing does
    not stop the others."""
    header, rows = record.load(directory)
    traced = [r for r in rows if r.get("span_id")]
    report = {"run_id": header["run_id"], "rows": len(rows), "traced": len(traced),
              "tools": {}}
    for name, send in senders.items():
        try:
            sent, errors = send(traced, header.get("environment", "dev"), call)
        except (OSError, KeyError) as error:
            sent, errors = 0, [f"{type(error).__name__}: {error}"]
        report["tools"][name] = {"sent": sent, "errors": errors}
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Send a run's verdicts to the tools as grades.")
    parser.add_argument("--run", help="run id or directory; default the latest run")
    parser.add_argument("--runs-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    runs_dir = Path(args.runs_dir or record.DEFAULT_RUNS_DIR)
    if args.run:
        directory = Path(args.run) if Path(args.run).is_dir() else runs_dir / args.run
    else:
        directory = record.latest(runs_dir)
    if directory is None or not (directory / record.HEADER_FILE).exists():
        raise SystemExit(f"no run at {directory}")

    report = send_run(directory, senders_from_env())
    print(f"run {report['run_id']}: {report['traced']} of {report['rows']} rows traced")
    if report["traced"] < report["rows"]:
        print("  untraced rows have no trace to grade — skipped")
    failed = False
    for name, result in report["tools"].items():
        print(f"  {name:<10} {result['sent']} grades sent"
              + (f", {len(result['errors'])} failed" if result["errors"] else ""))
        for error in result["errors"][:3]:
            print(f"    {error}")
        failed |= bool(result["errors"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
