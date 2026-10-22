"""Verdicts become each tool's native grade, on the right trace, re-sendably.

No tool needs to be running: requests go to a fake `call` that records them.
The smith id mapping is pinned to an id read back from smith's API.
"""

import json

import pytest

from evals import scores

ROW = {"case_id": "lakers-ppg-2022-23", "attempt": 1, "verdict": "wrong",
       "detail": "values differ", "trace_id": "7cf80543429c9223eccf46cf03ed4458",
       "span_id": "15d103ff9f2dfc5d"}


class FakeTools:
    """Answers every request with a fixed status per URL fragment."""

    def __init__(self, statuses=None):
        self.statuses = statuses or {}
        self.requests = []

    def __call__(self, method, url, headers, body=None):
        self.requests.append((method, url, body))
        for fragment, status in self.statuses.items():
            if fragment in url and (method, status) != ("PATCH", 409):
                return status, "{}"
        return 200, "{}"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phx:6006")
    monkeypatch.setenv("LANGFUSE_HOST", "http://fuse:3100")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls")


def write_run(tmp_path, rows):
    directory = tmp_path / "run-1"
    directory.mkdir()
    (directory / "header.json").write_text(json.dumps({"run_id": "run-1", "environment": "eval"}))
    (directory / "cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return directory


# --- the grades --------------------------------------------------------------

@pytest.mark.parametrize("verdict, label", [
    ("exact", "pass"), ("tolerance", "pass"), ("refusal_correct", "pass"),
    ("wrong", "fail"), ("superset", "fail"), ("error", "fail"), ("refusal_absent", "fail"),
])
def test_pass_follows_the_runners_pass_line(verdict, label):
    verdict_grade, pass_grade = scores.grades(ROW | {"verdict": verdict})
    assert (verdict_grade.name, verdict_grade.label, verdict_grade.score) == ("verdict", verdict, None)
    assert (pass_grade.name, pass_grade.label) == ("pass", label)
    assert pass_grade.score == (1.0 if label == "pass" else 0.0)


def test_smith_ids_are_the_span_id_zero_padded():
    # Read back from smith's API on 2026-10-05.
    assert scores.smith_run_id("15d103ff9f2dfc5d") == "00000000-0000-0000-15d1-03ff9f2dfc5d"


# --- payloads ----------------------------------------------------------------

def test_smith_feedback_lands_on_the_root_run():
    verdict, passed = (scores.smith_feedback(ROW, g) for g in scores.grades(ROW))
    assert verdict["run_id"] == verdict["trace_id"] == "00000000-0000-0000-15d1-03ff9f2dfc5d"
    assert (verdict["key"], verdict["value"], "score" not in verdict) == ("verdict", "wrong", True)
    assert (passed["key"], passed["score"], "value" not in passed) == ("pass", 0.0, True)
    # Not extended: a graded trace stays on the free 14-day tier.
    assert "extend_trace_retention" not in verdict


def test_fuse_scores_are_typed_and_carry_the_runs_environment():
    verdict, passed = (scores.fuse_score(ROW, g, "eval") for g in scores.grades(ROW))
    assert (verdict["dataType"], verdict["value"]) == ("CATEGORICAL", "wrong")
    assert (passed["dataType"], passed["value"]) == ("BOOLEAN", 0.0)
    assert verdict["traceId"] == ROW["trace_id"]
    assert verdict["environment"] == "eval"
    # On the root step, beside hand labels and where fuse reads experiment scores.
    assert verdict["observationId"] == ROW["span_id"]


def test_phx_annotates_the_root_span_as_code():
    verdict, passed = (scores.phx_annotation(ROW, g) for g in scores.grades(ROW))
    assert verdict == {"span_id": ROW["span_id"], "name": "verdict", "annotator_kind": "CODE",
                       "result": {"label": "wrong", "explanation": "values differ"}}
    assert passed["result"] == {"label": "fail", "score": 0.0}


def test_a_grade_has_the_same_id_every_time_it_is_sent():
    grade = scores.grades(ROW)[0]
    assert scores.smith_feedback(ROW, grade)["id"] == scores.smith_feedback(dict(ROW), grade)["id"]
    assert scores.fuse_score(ROW, grade, "dev")["id"] == f"{ROW['trace_id']}-verdict"
    other = ROW | {"span_id": "0000000000000001"}
    assert scores.smith_feedback(other, grade)["id"] != scores.smith_feedback(ROW, grade)["id"]


# --- sending a run -------------------------------------------------------------

def test_every_traced_row_is_graded_in_every_tool(env, tmp_path):
    untraced = ROW | {"trace_id": None, "span_id": None}
    directory = write_run(tmp_path, [ROW, ROW | {"verdict": "exact"}, untraced])
    tools = FakeTools()
    report = scores.send_run(directory, scores.senders_from_env(), call=tools)
    assert (report["rows"], report["traced"]) == (3, 2)
    assert {n: t["sent"] for n, t in report["tools"].items()} == {
        "phoenix": 4, "langfuse": 4, "langsmith": 4}
    phx = [body for _, url, body in tools.requests if "span_annotations" in url]
    assert len(phx) == 1 and len(phx[0]["data"]) == 4      # one batch


def test_a_resent_smith_grade_is_updated_not_duplicated(env, tmp_path):
    directory = write_run(tmp_path, [ROW])
    tools = FakeTools({"/api/v1/feedback": 409})
    report = scores.send_run(directory, {"langsmith": scores.send_smith}, call=tools)
    patches = [(url, body) for method, url, body in tools.requests if method == "PATCH"]
    assert report["tools"]["langsmith"] == {"sent": 2, "errors": []}
    assert len(patches) == 2 and patches[1][1] == {"score": 0.0, "comment": None}


def test_one_tool_failing_does_not_stop_the_others(env, tmp_path):
    directory = write_run(tmp_path, [ROW])
    tools = FakeTools({"phx:6006": 500})
    report = scores.send_run(directory, scores.senders_from_env(), call=tools)
    assert report["tools"]["phoenix"]["sent"] == 0 and report["tools"]["phoenix"]["errors"]
    assert report["tools"]["langfuse"]["sent"] == 2
    assert report["tools"]["langsmith"]["sent"] == 2


def test_a_tool_is_graded_only_when_its_settings_are_present(monkeypatch):
    for name in ("PHOENIX_COLLECTOR_ENDPOINT", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
                 "LANGFUSE_HOST", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert scores.senders_from_env() == {}
    monkeypatch.setenv("LANGFUSE_HOST", "http://fuse:3100")
    assert scores.senders_from_env() == {}
