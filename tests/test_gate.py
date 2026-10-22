"""The gate fails a run that slipped below its baseline, and only then."""

import json

import pytest

from evals import gate, record

SHA = "65b6412234d2d8817678d04f7cd2548aec82fec02b6c5b86679cade7572c21c7"


def write_run(tmp_path, run_id, verdicts: dict[str, list[str]], sha=SHA, **config):
    """A run folder: one row per case attempt, verdicts in attempt order."""
    directory = tmp_path / run_id
    directory.mkdir()
    header = {"run_id": run_id, "snapshot_sha256": sha,
              "config": {"name": "local-docs-r3", "model": "qwen", **config}}
    (directory / record.HEADER_FILE).write_text(json.dumps(header))
    rows = [{"case_id": case, "attempt": i + 1, "verdict": v}
            for case, vs in verdicts.items() for i, v in enumerate(vs)]
    (directory / record.CASES_FILE).write_text("".join(json.dumps(r) + "\n" for r in rows))
    return directory


BASE = {"a": ["exact"] * 3, "b": ["exact", "wrong", "exact"], "c": ["wrong"] * 3,
        "d": ["exact"] * 3, "e": ["wrong"] * 3}          # 8 of 15 = 53.3%
GATE = gate.GateConfig(name="t", margin_points=10)


def run_gate(tmp_path, new_verdicts, **kwargs):
    base = write_run(tmp_path, "base", BASE)
    new = write_run(tmp_path, "new", new_verdicts, **kwargs)
    return gate.evaluate(base, new, GATE)


def checks(result) -> dict:
    return {c["name"]: c for c in result["checks"]}


def test_the_same_result_passes(tmp_path):
    assert run_gate(tmp_path, BASE)["passed"]


def test_noise_within_the_margin_passes(tmp_path):
    # One attempt lost: 7 of 15 = 46.7%, above the 43.3% line.
    result = run_gate(tmp_path, BASE | {"b": ["exact", "wrong", "wrong"]})
    assert result["passed"]


def test_a_drop_below_the_line_fails(tmp_path):
    # Three attempts lost: 5 of 15 = 33.3%, below 43.3%.
    result = run_gate(tmp_path, BASE | {"b": ["wrong"] * 3, "d": ["exact", "wrong", "exact"]})
    assert not result["passed"]
    assert not checks(result)["score line"]["passed"]


def test_losing_a_stable_case_fails_even_when_the_score_holds(tmp_path):
    # "a" goes 3/3 to 0/3, while "c" and "e" gain enough to hold the rate.
    result = run_gate(tmp_path, BASE | {"a": ["wrong"] * 3, "c": ["exact"] * 3})
    assert checks(result)["score line"]["passed"]
    stable = checks(result)["no lost stable case"]
    assert not stable["passed"]
    assert stable["cases"] == ["a"]


def test_a_stable_case_dropping_to_two_of_three_is_noise(tmp_path):
    result = run_gate(tmp_path, BASE | {"a": ["exact", "wrong", "exact"]})
    assert checks(result)["no lost stable case"]["passed"]


def test_different_snapshots_cannot_be_compared(tmp_path):
    with pytest.raises(gate.GateError, match="snapshot"):
        run_gate(tmp_path, BASE, sha="0" * 64)


def test_different_cases_cannot_be_compared(tmp_path):
    with pytest.raises(gate.GateError, match="cases"):
        run_gate(tmp_path, {k: v for k, v in BASE.items() if k != "e"})


def test_different_attempt_counts_cannot_be_compared(tmp_path):
    with pytest.raises(gate.GateError, match="attempts"):
        run_gate(tmp_path, {k: v[:1] for k, v in BASE.items()})


def test_the_change_under_test_is_reported(tmp_path):
    result = run_gate(tmp_path, BASE, model="sol")
    assert result["config_changes"] == {"model": ["qwen", "sol"]}


def test_the_baseline_comes_from_the_gate_config():
    pinned = gate.GateConfig(name="t", baselines={"local-docs-r3": "base-run"})
    assert gate.baseline_for({"config": {"name": "local-docs-r3"}}, pinned) == "base-run"
    with pytest.raises(gate.GateError, match="no baseline"):
        gate.baseline_for({"config": {"name": "other"}}, pinned)


def test_the_cli_writes_its_result_and_exits_nonzero_on_failure(tmp_path):
    base = write_run(tmp_path, "base", BASE)
    new = write_run(tmp_path, "new", BASE | {"a": ["wrong"] * 3})
    code = gate.main(["--run", str(new), "--baseline", str(base), "--gate", "default"])
    assert code == 1
    assert json.loads((new / gate.OUT_FILE).read_text())["passed"] is False


def test_the_shipped_gate_config_loads():
    loaded = gate.load_gate("default")
    assert loaded.margin_points > 0
    assert {"local-docs-r3", "openai-docs-r3"} <= loaded.baselines.keys()
