"""The report's numbers come from the run record, and a bad template stops it."""

import json

import pytest

from evals import record, report


def test_every_placeholder_is_filled():
    assert report.fill("a {{x}} b {{y}}", {"x": "1", "y": "2"}) == "a 1 b 2"


def test_an_unknown_placeholder_stops_the_build():
    with pytest.raises(report.ReportError, match="typo"):
        report.fill("{{typo}}", {"x": "1"})


def test_the_shipped_template_uses_only_known_placeholders():
    template = (report.ROOT / "docs/report/sref_eval_template.md").read_text()
    known = {"arms", "metrics", "grid", "taxonomy", "judge", "generated"}
    assert set(report.PLACEHOLDER.findall(template)) <= known


def test_the_grid_shows_each_attempt_and_the_passes():
    arm = {"label": "m", "rows": [
        {"case_id": "c", "attempt": 2, "verdict": "wrong"},
        {"case_id": "c", "attempt": 1, "verdict": "exact"},
    ]}
    grid = report.grid_table([arm], {"c": "trap"})
    assert "exact · wrong (**1/2**)" in grid


def test_a_missing_run_is_named(tmp_path):
    with pytest.raises(report.ReportError, match="no run"):
        report.load_run(tmp_path, "nope")


def test_metrics_table_reads_the_run_record(tmp_path):
    directory = tmp_path / "r1"
    directory.mkdir()
    (directory / record.HEADER_FILE).write_text(json.dumps({"run_id": "r1", "config": {}}))
    rows = [{"case_id": "c", "attempt": 1, "verdict": "wrong", "model_elapsed_s": 2.0}]
    (directory / record.CASES_FILE).write_text(json.dumps(rows[0]) + "\n")
    header, loaded = report.load_run(tmp_path, "r1")
    arm = {"label": "m", "header": header, "rows": loaded,
           "summary": report.metrics.summarize(loaded)}
    text = report.metrics_table([arm])
    assert "| **Silent-failure rate** | 1/1 (100%) |" in text
